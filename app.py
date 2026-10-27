# -*- coding: utf-8 -*-
"""Echo —— 无线设备电量台

入口：单实例 → 建窗 → 起托盘 → 供 JS 调用。
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
import traceback
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import webview                                            # noqa: E402

from core import devices as devmod                        # noqa: E402
from core import protocols                                # noqa: E402
from core import winicon                                  # noqa: E402
from core.pushmon import MONITOR as PUSH                  # noqa: E402
from core.card import HoverCard, size as card_size        # noqa: E402
from core.store import Store, app_dir, protocols_path     # noqa: E402
from core.tray import Tray                                # noqa: E402

APP_NAME = "Echo"
TITLE = "Echo"
VERSION = "1.1.0"
ICON_PATH = os.path.join(app_dir(), "echo.ico")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
dwmapi = ctypes.WinDLL("dwmapi")

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = wintypes.LPARAM
user32.PostMessageW.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.ReleaseCapture.argtypes = []
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_uint]
user32.GetDpiForWindow.restype = ctypes.c_uint
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetForegroundWindow.argtypes = []
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.SetActiveWindow.restype = wintypes.HWND
user32.SetActiveWindow.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.SetFocus.argtypes = [wintypes.HWND]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetCurrentThreadId.argtypes = []
user32.GetDpiForSystem.restype = ctypes.c_uint
user32.GetDpiForSystem.argtypes = []
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

SW_HIDE, SW_SHOW = 0, 5


# 无边框窗口想被系统当成"可拉伸窗口"，必须补上 WS_THICKFRAME；
# 之后把手柄的按下动作转成 WM_NCLBUTTONDOWN + HT*，系统自己跑缩放循环
# （带贴边吸附、多屏、DPI 换算），比用 JS 算尺寸顺滑得多。
GWL_STYLE, WS_THICKFRAME = -16, 0x00040000
WM_NCLBUTTONDOWN = 0x00A1
SWP_RESIZE = 0x0001 | 0x0002 | 0x0004 | 0x0020   # NOSIZE|NOMOVE|NOZORDER|FRAMECHANGED
HT_EDGE = {"w": 10, "e": 11, "n": 12, "nw": 13, "ne": 14,
           "s": 15, "sw": 16, "se": 17}

HOLDER: dict = {}          # 运行期共享：窗口、托盘


# ------------------------------------------------------------------ 开机自启

def _run_key():
    import winreg
    return winreg, winreg.HKEY_CURRENT_USER, \
        r"Software\Microsoft\Windows\CurrentVersion\Run"


def startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(exe):
        exe = sys.executable
    return f'"{exe}" "{os.path.join(HERE, "app.py")}" --tray'


def autostart_enabled() -> bool:
    try:
        winreg, root, path = _run_key()
        with winreg.OpenKey(root, path) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except Exception:
        return False


def set_autostart(on: bool) -> bool:
    try:
        winreg, root, path = _run_key()
        with winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, startup_command())
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ 窗口外观

def find_hwnd(title: str = TITLE) -> int | None:
    """找主窗口。

    托盘还有一个同名隐藏窗口，FindWindow 可能先抓到它，导致"点托盘没反应"。
    这里枚举所有同名窗口，取面积最大的那个。
    """
    best: list = [0, 0]
    EnumProc = WNDENUMPROC

    def cb(h, _):
        buf = ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(h, buf, 64)
        if buf.value != title:
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(h, cls, 64)
        if not cls.value.startswith("WindowsForms"):
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        area = (r.right - r.left) * (r.bottom - r.top)
        if area > best[1]:
            best[0], best[1] = int(h), area
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return best[0] or None


def style_window() -> None:
    hwnd = find_hwnd()
    if not hwnd:
        return
    try:                                        # Windows 11 圆角
        pref = ctypes.c_int(2)
        dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 33,
                                     ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass
    try:                                        # 深色边框，避免亮边
        dark = ctypes.c_int(1)
        dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 20,
                                     ctypes.byref(dark), ctypes.sizeof(dark))
    except Exception:
        pass
    try:                                        # 装上可拉伸边框
        style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        if not style & WS_THICKFRAME:
            user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style | WS_THICKFRAME)
            user32.SetWindowPos(wintypes.HWND(hwnd), None, 0, 0, 0, 0, SWP_RESIZE)
    except Exception:
        pass


def dpi_scale(hwnd: int | None = None) -> float:
    try:
        dpi = user32.GetDpiForWindow(wintypes.HWND(hwnd)) if hwnd else 0
        if not dpi:
            dpi = user32.GetDpiForSystem()
    except Exception:
        dpi = 96
    return (dpi or 96) / 96.0


def force_foreground(hwnd: int) -> None:
    """把窗口真的拿到前台。

    光 SetForegroundWindow 常被系统的前台锁挡掉：窗口看着出来了，但其实没激活，
    于是接下来几次点击都被"激活"这一步吃掉 —— 表现就是"要拖三四次才动"。
    这里用 AttachThreadInput 临时接上前台线程的输入队列，绕开这个限制。
    """
    if not hwnd:
        return
    try:
        if user32.GetForegroundWindow() == hwnd:
            return
        fg = user32.GetForegroundWindow()
        tid_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        tid_me = kernel32.GetCurrentThreadId()
        attached = False
        if tid_fg and tid_fg != tid_me:
            attached = bool(user32.AttachThreadInput(tid_fg, tid_me, True))
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetActiveWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(tid_fg, tid_me, False)
    except Exception:
        pass


def _win_size(win):
    try:
        return (int(win.width), int(win.height))
    except Exception:
        return (0, 0)


def _win_top(win):
    try:
        return win.on_top
    except Exception:
        return None


def dpi_at(x: int, y: int) -> float:
    """该坐标所在显示器的真实缩放比（不受进程 DPI 感知模式影响）。"""
    try:
        shcore = ctypes.WinDLL("shcore")
        hmon = user32.MonitorFromPoint(wintypes.POINT(x, y), 2)
        dx, dy = ctypes.c_uint(0), ctypes.c_uint(0)
        if shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0 and dx.value:
            return dx.value / 96.0
    except Exception:
        pass
    return dpi_scale()


def work_area(x: int, y: int):
    """光标附近那块屏幕的可用区域（物理像素）。"""
    try:
        MONITOR_DEFAULTTONEAREST = 2
        pt = wintypes.POINT(x, y)
        hmon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcWork
            return (r.left, r.top, r.right, r.bottom)
    except Exception:
        pass
    return (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))


# ------------------------------------------------------------------ 桥

class Api:
    def __init__(self, store: Store, window_holder: dict):
        self.store = store
        self.win = window_holder
        self._lock = threading.Lock()
        self._cache: list[devmod.Device] = []
        self._at = 0.0
        self._scanning = False
        self._drag_thread = None
        self._resize_thread = None
        self._card_visible = False
        self._card_at = 0.0
        self._watching = False
        self._close_asked = False

    # -- 数据 ------------------------------------------------------
    def _user_specs(self):
        return protocols.load_user_specs(protocols_path())

    def _apply_config(self, devs: list[devmod.Device]) -> list[dict]:
        order = self.store.settings.get("order") or []
        if order:
            pos = {k: i for i, k in enumerate(order)}

            def rank(d):
                # 已连接的永远排前面；同一组内才看用户拖出来的顺序
                online = 0 if d.level is not None else 1
                if d.key in pos:
                    return (online, 1.0 + pos[d.key])
                return (online, 0.5 if online == 0 else 1e6)
            devs = sorted(devs, key=rank)
        out = []
        for d in devs:
            cfg = self.store.devices.get(d.key, {})
            if cfg.get("hidden"):
                continue
            if cfg.get("name"):
                d.name = cfg["name"]
            if cfg.get("icon"):
                d.kind = cfg["icon"]
            item = d.to_dict()
            item["renamed"] = bool(cfg.get("name"))
            item["icon_custom"] = bool(cfg.get("icon"))
            out.append(item)
        return out

    def _do_scan(self) -> None:
        with self._lock:
            if self._scanning:
                return
            self._scanning = True
        try:
            devs = devmod.scan(self._user_specs())
            with self._lock:
                self._cache = devs
                self._at = time.time()
        except Exception:
            # 窗口模式下没有控制台，异常必须落盘，否则用户只看到"什么都没有"
            try:
                with open(os.path.join(app_dir(), "error.log"), "a",
                          encoding="utf-8") as f:
                    f.write(time.strftime("[%Y-%m-%d %H:%M:%S] scan\n"))
                    f.write(traceback.format_exc() + "\n")
            except Exception:
                pass
            traceback.print_exc()
        finally:
            with self._lock:
                self._scanning = False
        self._drag_thread = None
        self._resize_thread = None
        self._card_visible = False
        self._card_at = 0.0
        self._watching = False
        self._close_asked = False

    def _apply_push(self) -> None:
        """把监听线程收到的最新上报合并进缓存，避免为等一条推送去重新枚举设备。"""
        now = time.time()
        with self._lock:
            devs = list(self._cache)
        for d in devs:
            if not d.key.startswith("hid:"):
                continue
            r = PUSH.get(d.key[4:])
            if r is None or (d.level is not None and d.updated >= r.ts):
                continue
            d.last_level = r.level
            d.updated = r.ts
            asleep = now - r.ts > 8
            d.level, d.charging, d.connected = r.level, r.charging, True
            d.source = r.source
            d.error = ""
            d.extra["asleep"] = asleep

    def state(self, force: bool = False) -> dict:
        interval = max(15, int(self.store.settings.get("interval", 60)))
        stale = time.time() - self._at > interval
        if force or stale or not self._cache:
            self._do_scan()
        else:
            self._apply_push()
        with self._lock:
            devs = list(self._cache)
            at = self._at
        return {
            "devices": self._apply_config(devs),
            "at": int(at),
            "scanned": int(time.time() - at) if at else None,
            "settings": dict(self.store.settings),
            "autostart": autostart_enabled(),
            "version": VERSION,
            "platform": "Windows",
        }

    def refresh(self) -> dict:
        self._do_scan()
        return self.state(force=False)

    # -- 编辑 ------------------------------------------------------
    def set_name(self, key: str, name: str) -> dict:
        name = (name or "").strip()[:32]
        self.store.set_device(key, "name", name or None)
        return self.state()

    def set_icon(self, key: str, icon: str) -> dict:
        allowed = set(devmod.KIND_LABEL)
        if icon not in allowed:
            return self.state()
        self.store.set_device(key, "icon", icon)
        return self.state()

    def reset_device(self, key: str) -> dict:
        self.store.devices.pop(key, None)
        self.store.save()
        return self.state()

    def set_setting(self, key: str, value):
        if key == "autostart":
            ok = set_autostart(bool(value))
            self.store.set_setting("autostart", bool(value))
            return {"ok": ok, "state": self.state()}
        if key == "interval":
            try:
                value = max(15, min(1800, int(value)))
            except Exception:
                value = 60
        if key in ("minimize_to_tray", "theme", "tray_hint_shown", "interval"):
            self.store.set_setting(key, value)
        return self.state()

    # -- 诊断 ------------------------------------------------------
    def probe(self, key: str) -> dict:
        if not key.startswith("hid:"):
            return {"ok": False, "reason": "只有 2.4G 设备需要协议诊断"}
        group = key[4:]
        from core import hidwin as hw
        ifaces = [i for i in hw.enumerate_hid() if i.group == group]
        if not ifaces:
            return {"ok": False, "reason": "设备已不在，可能被拔出"}
        push = PUSH.get(group)
        if push is not None:
            # 已经有主动上报，不必再往设备里发探测帧
            return {
                "ok": True, "mode": "push",
                "vid_pid": ifaces[0].vid_pid,
                "manufacturer": ifaces[0].manufacturer or "",
                "usage": None, "in_len": None, "out_len": None, "feature_len": None,
                "frame": push.raw[:12].hex(), "level": push.level,
                "charging": push.charging, "source": push.source,
                "age": int(time.time() - push.ts),
                "interfaces": [{"usage": i.usage_text, "in": i.input_len,
                                "out": i.output_len, "feat": i.feature_len}
                               for i in ifaces],
            }
        target = sorted(ifaces, key=lambda i: (0 if i.is_vendor_page else 1,
                                              0 if i.output_len else 1))[0]
        info = protocols.probe_interface(target)
        info["mode"] = "probe"
        info["ok"] = True
        info["interfaces"] = [{"usage": i.usage_text, "in": i.input_len,
                               "out": i.output_len, "feat": i.feature_len}
                              for i in ifaces]
        return info

    # -- 窗口 ------------------------------------------------------
    def hide_window(self) -> None:
        hwnd = find_hwnd()
        if hwnd:
            user32.ShowWindow(hwnd, SW_HIDE)

    def start_drag(self, sx: float = -1, sy: float = -1) -> None:
        """拖窗口：自己跟鼠标。

        JS 把按下那一刻的屏幕坐标传进来（CSS px），这里换算成物理像素当原点，
        之后每 12ms 读一次光标 SetWindowPos，松手即停。这条路径实测能跟住鼠标，
        而 WM_NCLBUTTONDOWN/HTCAPTION、WM_SYSCOMMAND+SC_MOVE 在无边框窗上都不动。
        """
        if self._drag_thread and self._drag_thread.is_alive():
            return
        self._drag_thread = threading.Thread(target=self._drag_loop, args=(sx, sy),
                                             daemon=True)
        self._drag_thread.start()

    def _drag_loop(self, js_x: float = -1, js_y: float = -1) -> None:
        hwnd = find_hwnd()
        if not hwnd:
            return
        pt = wintypes.POINT()
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        wx, wy = rect.left, rect.top
        k = dpi_scale(hwnd)
        if js_x is not None and js_x >= 0 and js_y is not None and js_y >= 0:
            sx, sy = int(js_x * k), int(js_y * k)
        else:
            user32.GetCursorPos(ctypes.byref(pt))
            sx, sy = pt.x, pt.y
        try:
            while user32.GetAsyncKeyState(0x01) & 0x8000:
                user32.GetCursorPos(ctypes.byref(pt))
                user32.SetWindowPos(hwnd, None, wx + pt.x - sx, wy + pt.y - sy,
                                    0, 0, 0x0001 | 0x0004 | 0x0010)
                time.sleep(0.012)
        finally:
            self.save_geom()

    def set_order(self, keys) -> dict:
        keys = [str(k)[:160] for k in list(keys or [])][:64]
        self.store.set_setting("order", keys)
        return self.state()

    def start_resize(self, edge: str = "se", sx: float = -1, sy: float = -1) -> None:
        """拖边框改大小：和拖窗口同一套做法 —— 自己在后台线程跟鼠标。

        为什么不再用 WM_NCLBUTTONDOWN + HT*：那是"把活交给系统的模态循环"，
        但鼠标按下发生在 WebView 子窗里，捕获(setCapture)属于浏览器那个线程，
        我们这边 ReleaseCapture() 只是给自己线程松手，抢不过来。于是：
        有时系统的循环拿不到鼠标移动 → 拖着没反应，松手后什么都没变；
        有时恰好没被浏览器抓住 → 又能用。这就是"时好时坏、要拖三四次"。
        自己算尺寸就没有这些前提条件，只要按下那一刻的坐标是真的，就一定跟手。
        """
        if self._resize_thread and self._resize_thread.is_alive():
            return
        self._resize_thread = threading.Thread(
            target=self._resize_loop, args=(edge, sx, sy), daemon=True)
        self._resize_thread.start()

    def _resize_loop(self, edge: str, js_x: float, js_y: float) -> None:
        hwnd = find_hwnd()
        if not hwnd:
            return
        force_foreground(hwnd)
        pt = wintypes.POINT()
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        l, t, r, b = rect.left, rect.top, rect.right, rect.bottom
        k = dpi_scale(hwnd)
        if js_x is not None and js_x >= 0 and js_y is not None and js_y >= 0:
            sx, sy = int(js_x * k), int(js_y * k)
        else:
            user32.GetCursorPos(ctypes.byref(pt))
            sx, sy = pt.x, pt.y

        edge = str(edge or "se")
        west, east = "w" in edge, "e" in edge
        north, south = "n" in edge, "s" in edge
        min_w, min_h = int(300 * k), int(96 * k)     # 和 create_window 的 min_size 一致

        try:
            while user32.GetAsyncKeyState(0x01) & 0x8000:
                user32.GetCursorPos(ctypes.byref(pt))
                dx, dy = pt.x - sx, pt.y - sy
                nl, nt, nw, nh = l, t, r - l, b - t
                if west:
                    nl = min(l + dx, r - min_w)
                    nw = r - nl
                elif east:
                    nw = max(min_w, r - l + dx)
                if north:
                    nt = min(t + dy, b - min_h)
                    nh = b - nt
                elif south:
                    nh = max(min_h, b - t + dy)
                user32.SetWindowPos(hwnd, None, nl, nt, nw, nh,
                                    0x0004 | 0x0010)      # NOZORDER | NOACTIVATE
                time.sleep(0.012)
        finally:
            self.save_geom()

    def set_mode(self, mini: bool, height: int = 0) -> dict:
        """切换紧凑 / 完整。只调整尺寸，不碰置顶状态、不重新扫描
        （之前每次切换都跑一次 state()，里面有同步扫描，界面就卡住了）。"""
        win = self.win.get("window")
        mini = bool(mini)
        self.store.set_setting("mini", mini)
        if win:
            try:
                if mini:
                    if not self.store.settings.get("full_geom"):
                        self.store.set_setting("full_geom", self._geom())
                    h = max(64, min(560, int(height or 180)))
                    if _win_size(win) != (332, h):
                        if _win_top(win) is not True:
                            win.on_top = True
                        win.resize(332, h)
                else:
                    g = self.store.settings.get("geom") or {}
                    w, h = int(g.get("w") or 640), int(g.get("h") or 624)
                    if _win_size(win) != (w, h):
                        if _win_top(win) is not False:
                            win.on_top = False
                        win.resize(w, h)
            except Exception:
                pass
        return {"mini": mini, "ok": True}

    def _geom(self) -> dict:
        win = self.win.get("window")
        if not win:
            return {}
        try:
            return {"w": int(win.width), "h": int(win.height),
                    "x": int(win.x), "y": int(win.y)}
        except Exception:
            return {}

    def save_geom(self) -> None:
        """关窗/松手时记一下位置尺寸，下次原地打开。"""
        if self.store.settings.get("mini"):
            return
        g = self._geom()
        if g:
            self.store.set_setting("geom", g)

    def show_window(self) -> None:
        hwnd = find_hwnd()
        if hwnd:
            user32.ShowWindow(hwnd, SW_SHOW)
            force_foreground(hwnd)

    # -- 悬停卡片 --------------------------------------------------
    def _scale(self) -> float:
        """屏幕缩放比：用主窗口的物理宽 / 逻辑宽算，绕开各种 DPI 感知模式的坑。"""
        win = self.win.get("window")
        hwnd = find_hwnd()
        try:
            r = wintypes.RECT()
            if hwnd and win and user32.GetWindowRect(hwnd, ctypes.byref(r)):
                phys = r.right - r.left
                logical = int(win.width)
                if logical > 50 and phys > 50:
                    return max(1.0, round(phys / logical, 2))
        except Exception:
            pass
        return 1.0

    def _card_rows(self):
        with self._lock:
            devs = list(self._cache)
        items = self._apply_config(devs)
        live = [d for d in items
                if d.get("level") is not None and d.get("connected") is not False]
        rows = []
        for d in live[:6]:
            lv = d["level"]
            band = "ok" if lv > 30 else "warn" if lv >= 10 else "low"
            val = ("⚡" if d.get("charging") and lv < 100 else "") + f"{lv}%"
            rows.append((d["name"], val, band))
        more = max(0, len(live) - 6)
        head = f"{len(live)} 台在线" + (f" · 另有 {more} 台" if more else "")
        return rows, head

    def show_card(self) -> None:
        card = HOLDER.get("card")
        if card is None:
            card = HOLDER["card"] = HoverCard()
        tray = HOLDER.get("tray")
        if tray is None:
            return
        ir = tray.icon_rect()
        if not ir:
            return
        rows, head = self._card_rows()
        k = dpi_scale(find_hwnd())
        w, h = card_size(len(rows), k)
        icx = (ir[0] + ir[2]) // 2                 # 图标中心 = 卡片中心，不再偏
        left, top, right, bottom = work_area(icx, ir[1])
        x = max(left + 4, min(icx - w // 2, right - w - 4))
        y = ir[1] - h - int(6 * k)
        if y < top + 4:                            # 任务栏在上边时改放下方
            y = ir[3] + int(6 * k)
        theme = "light" if str(self.store.settings.get("theme")) == "light" else "dark"
        card.show(rows, head, x, y, k, theme)
        self._card_visible = True
        self._close_asked = False
        self._card_at = time.time()
        if not self._watching:
            self._watching = True
            threading.Thread(target=self._watch_card, daemon=True).start()

    def card_open(self) -> None:
        self.hide_card()
        self.show_window()

    def hide_card(self) -> None:
        card = HOLDER.get("card")
        if card is not None:
            card.hide()
        self._card_visible = False

    def hover_tick(self, show: bool = True) -> None:
        """托盘的 NIN_POPUPOPEN / NIN_POPUPCLOSE。

        POPUPCLOSE 偶尔会在鼠标其实还停在图标上时误发（比如刚弹出来那一刻），
        所以这里不当场收，交给看门狗按光标真实位置判断，约 0.25 秒后收。
        """
        if show:
            self.show_card()
        else:
            self._close_asked = True

    def _watch_card(self) -> None:
        """鼠标离开图标和卡片就收（系统偶尔不发 POPUPCLOSE，这里兜底）。"""
        outside = 0
        while True:
            time.sleep(0.12)
            if not self._card_visible:
                self._watching = False
                return
            pt = wintypes.POINT()
            if not user32.GetCursorPos(ctypes.byref(pt)):
                continue
            keep = False
            card = HOLDER.get("card")
            hwnd = card.hwnd if card is not None else 0
            r = wintypes.RECT()
            if hwnd and user32.GetWindowRect(hwnd, ctypes.byref(r)):
                keep = r.left <= pt.x <= r.right and r.top <= pt.y <= r.bottom
            if not keep:
                tray = HOLDER.get("tray")
                ir = tray.icon_rect() if tray else None
                if ir:
                    keep = ir[0] <= pt.x <= ir[2] and ir[1] <= pt.y <= ir[3]
            outside = 0 if keep else outside + 1
            # 系统明确说过"移出了"就收得快一点，否则留一点余量防止误收
            if outside >= (1 if self._close_asked else 2):
                self.hide_card()
                self._watching = False
                return

    # -- 扩展 ------------------------------------------------------
    def brief(self) -> str:
        """给 AI 看的说明书全文：复制出去，别的 agent 就能接着干活。"""
        for p in (os.path.join(HERE, "docs", "AGENT.md"),
                  os.path.join(HERE, "AGENT.md")):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                continue
        return ""

    def sniff(self, seconds: int = 8) -> dict:
        """通用嗅探：不听协议、直接抓原始帧，给未知接收器找电量字节。"""
        try:
            from core.sniff import sniff as run
            return run(max(2, min(20, int(seconds))))
        except Exception as e:
            return {"error": str(e), "interfaces": []}

    def quit_app(self) -> None:
        """真正退出：先摘掉托盘图标，再结束进程（否则任务栏会留下幽灵图标）。"""
        try:
            self.store.save()
        except Exception:
            pass
        tray = HOLDER.get("tray")
        if tray is not None:
            try:
                tray.destroy()
            except Exception:
                pass
            time.sleep(0.15)
        os._exit(0)

    def open_data_dir(self) -> None:
        os.startfile(app_dir())

    def copy_text(self, text: str) -> None:
        """写剪贴板。GMEM_MOVEABLE 的句柄是 64 位指针，必须声明 restype，
        否则 ctypes 默认按 int 返回、句柄被截断，SetClipboardData 静默失败。"""
        data = str(text).encode("utf-16-le") + b"\x00\x00"
        for _ in range(6):
            if user32.OpenClipboard(None):
                break
            time.sleep(0.03)
        else:
            return
        try:
            user32.EmptyClipboard()
            kernel32.GlobalAlloc.restype = wintypes.HANDLE
            kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
            kernel32.GlobalLock.restype = ctypes.c_void_p
            kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
            kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
            user32.SetClipboardData.restype = wintypes.HANDLE
            user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
            h = kernel32.GlobalAlloc(0x0042, len(data))
            if not h:
                return
            ptr = kernel32.GlobalLock(h)
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(h)
            user32.SetClipboardData(13, h)      # CF_UNICODETEXT
        finally:
            user32.CloseClipboard()


# ------------------------------------------------------------------ 自检

def selftest() -> int:
    """Echo.exe --selftest：不开界面，把当前能读到的东西写成报告。

    排障用。"看不到设备"时先跑这个，报告里有每个接口和每台设备的结论。
    """
    lines = [f"Echo {VERSION} 自检  {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"Python {sys.version.split()[0]}  冻结={getattr(sys, 'frozen', False)}",
             f"WebView2 运行时: {'已安装' if _webview2_version() else '未检测到'}", ""]
    try:
        from core import audioend
        lines.append("== 音频端点 ==")
        for name, state in audioend.snapshot():
            lines.append(f"  state={state:<10} {name}")
        lines.append("")
    except Exception as e:
        lines.append(f"音频端点读取失败: {e}")

    try:
        from core import hidwin as hw
        lines.append("== HID 接口 ==")
        for i in hw.enumerate_hid():
            if "vid_" not in i.path.lower() and "bthledevice" not in i.path.lower():
                continue
            lines.append(f"  {i.vid_pid}  {i.usage_text}  入{i.input_len} 出{i.output_len} "
                         f"特性{i.feature_len}  {i.product!r}")
        lines.append("")
    except Exception as e:
        lines.append(f"HID 枚举失败: {e}")

    try:
        specs = protocols.load_user_specs(protocols_path())
        devmod.scan(specs)             # 先挂上监听
        deadline = time.time() + 7     # 主动上报的设备约 2 秒推一包，等它
        while time.time() < deadline:
            if any(d.level is not None for d in devmod.scan(specs) if d.link == "2.4g"):
                break
            time.sleep(1)
        lines.append("== 设备结论 ==")
        for d in devmod.scan(specs):
            lines.append(f"  {d.name} | {d.kind} | 电量={d.level} 上次={d.last_level} "
                         f"连接={d.connected} | {d.source} | {d.detail}")
    except Exception:
        lines.append("扫描失败:\n" + traceback.format_exc())

    report = "\n".join(lines)
    path = os.path.join(app_dir(), "selftest.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    try:
        print(report)
    except Exception:
        pass
    try:
        os.startfile(path)
    except Exception:
        pass
    return 0


def _webview2_version() -> str:
    try:
        import winreg
        key = (r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"
               r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            return winreg.QueryValueEx(k, "pv")[0]
    except Exception:
        return ""


# ------------------------------------------------------------------ 主流程

def main() -> None:
    if "--selftest" in sys.argv:
        sys.exit(selftest())

    kernel32.CreateMutexW(None, False, "Local\\Echo-SingleInstance")
    if ctypes.get_last_error() == 183:                     # ERROR_ALREADY_EXISTS
        hwnd = find_hwnd()
        if hwnd:
            user32.ShowWindow(hwnd, SW_SHOW)
            force_foreground(hwnd)
        return

    # 先把进程设成 per-monitor v2，否则 Windows 会把整窗按 96dpi 虚拟化，
    # 在高分屏上界面会明显偏小、坐标也对不上
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

    try:
        winicon.ensure_ico(ICON_PATH)
    except Exception:
        pass

    store = Store()
    api = Api(store, HOLDER)

    start_hidden = "--tray" in sys.argv
    mini = bool(store.settings.get("mini"))

    geom = store.settings.get("geom") or {}
    width = int(geom.get("w") or 640)
    height = int(geom.get("h") or 624)
    wx, wy = geom.get("x"), geom.get("y")
    if wx is None or wy is None:
        try:
            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
            wx, wy = max(0, (sw - width) // 2), max(0, (sh - height) // 3)
        except Exception:
            wx = wy = None
    if mini:
        width, height = 332, int(store.settings.get("mini_h") or 190)

    window = webview.create_window(
        TITLE,
        os.path.join(HERE, "ui", "index.html"),
        js_api=api,
        width=width, height=height, x=wx, y=wy,
        min_size=(300, 96),
        frameless=True,
        easy_drag=False,
        background_color="#0B0B0C",
        resizable=True,
        on_top=mini,
        text_select=False,
    )
    HOLDER["window"] = window
    HOLDER["card"] = None
    tray_holder = HOLDER

    def tray_show():
        api.hide_card()
        hwnd = find_hwnd()
        if hwnd:
            user32.ShowWindow(hwnd, SW_SHOW)
            force_foreground(hwnd)

    def tray_refresh():
        threading.Thread(target=api._do_scan, daemon=True).start()
        try:
            if window and getattr(window, "evaluate_js", None):
                window.evaluate_js("window.Echo && window.Echo.pull()")
        except Exception:
            pass

    def tray_quit():
        api.quit_app()

    def tray_toggle_autostart():
        set_autostart(not autostart_enabled())
        try:
            window.evaluate_js("window.Echo && window.Echo.pull()")
        except Exception:
            pass

    def on_shown():
      try:
        style_window()
        hicon = winicon.tray_hicon(32)
        tray = Tray(APP_NAME, hicon, tray_show, tray_refresh, tray_quit,
                    autostart_enabled, tray_toggle_autostart,
                    on_hover=api.hover_tick)
        tray.start()
        tray_holder["tray"] = tray
        tray_holder["window"] = window
        api._do_scan()
      except Exception:
        with open(os.path.join(app_dir(), "error.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] on_shown\n"))
            f.write(traceback.format_exc() + "\n")

      if start_hidden:
            hwnd = find_hwnd()
            if hwnd:
                user32.ShowWindow(hwnd, SW_HIDE)


    window.events.shown += on_shown

    def on_closing():
        api.save_geom()
        if store.settings.get("minimize_to_tray", True):
            hwnd = find_hwnd()
            if hwnd:
                user32.ShowWindow(hwnd, SW_HIDE)
            return False
        tray_holder.get("tray") and tray_holder["tray"].destroy()
        return True

    window.events.closing += on_closing

    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", " ".join([
        "--disable-gpu",
        "--disable-gpu-compositing",
        "--renderer-process-limit=1",
        "--js-flags=--max-old-space-size=64",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-sync",
        "--disable-extensions",
        "--no-first-run",
        "--disable-features=Translate,OptimizationHints,MediaRouter,CalculateNativeWinOcclusion",
    ]))
    webview.start(debug=(os.environ.get("ECHO_DEBUG") == "1"), gui="edgechromium",
                  private_mode=False, storage_path=os.path.join(app_dir(), "webview"),
                  icon=ICON_PATH if os.path.exists(ICON_PATH) else None)



if __name__ == "__main__":
    try:
        main()
    except Exception:
        with open(os.path.join(app_dir(), "error.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S]\n"))
            f.write(traceback.format_exc() + "\n")
        raise
