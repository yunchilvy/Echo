# -*- coding: utf-8 -*-
"""echo/core/tray.py —— 通知区域图标（Shell_NotifyIconW + 原生弹出菜单）

在独立线程里跑自己的消息循环。点图标只做两件事：显示窗口、弹菜单，
窗口的显示/隐藏用 Win32 直接操作 HWND，不跨线程碰 UI 框架。
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_APP = 0x8000
WM_TRAY = WM_APP + 20
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04   # 已不用：悬停改由自绘卡片负责，留着备查
NIM_SETVERSION = 0x04
NOTIFYICON_VERSION_4 = 4
NIN_POPUPOPEN = 0x0406
NIN_POPUPCLOSE = 0x0407
NIM_ADD = 0x00
NIM_MODIFY = 0x01
NIM_DELETE = 0x02

MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
MF_CHECKED = 0x0008
MF_GRAYED = 0x0001
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080

ID_SHOW, ID_REFRESH, ID_AUTOSTART, ID_QUIT = 1001, 1002, 1003, 1004


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HANDLE),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_ubyte * 16),
                ("hBalloonIcon", wintypes.HANDLE)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("guidItem", ctypes.c_ubyte * 16)]


user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
shell32.Shell_NotifyIconGetRect.argtypes = [ctypes.POINTER(NOTIFYICONIDENTIFIER),
                                            ctypes.POINTER(wintypes.RECT)]
shell32.Shell_NotifyIconGetRect.restype = ctypes.c_long

user32.DefWindowProcW.restype = ctypes.c_void_p
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                  ctypes.c_void_p]
user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t,
                               wintypes.LPCWSTR]
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                      ctypes.POINTER(NOTIFYICONDATAW)]


class Tray:
    """一个托盘图标。on_show / on_refresh / on_toggle_autostart / on_quit 由外部提供。"""

    def __init__(self, title: str, hicon, on_show: Callable[[], None],
                 on_refresh: Callable[[], None], on_quit: Callable[[], None],
                 autostart_getter: Callable[[], bool],
                 on_toggle_autostart: Callable[[], None],
                 on_hover: Callable[[], None] = None):
        self.title = title
        self.hicon = hicon
        self.on_show = on_show
        self.on_refresh = on_refresh
        self.on_quit = on_quit
        self.autostart_getter = autostart_getter
        self.on_toggle_autostart = on_toggle_autostart
        self.on_hover = on_hover
        self._hwnd = None
        self._ready = threading.Event()
        self._wndproc_ref = WNDPROC(self._wndproc)
        self._thread = threading.Thread(target=self._run, name="echo-tray", daemon=True)

    # -------------------------------------------------- 外部接口
    def start(self) -> None:
        self._thread.start()
        self._ready.wait(3)

    def update_tip(self, text: str) -> None:
        """原生气泡提示已经不用了（悬停由自绘卡片接管），留个空实现。"""
        return

    def icon_rect(self):
        """托盘图标在屏幕上的矩形（物理像素）。取不到返回 None。"""
        if not self._hwnd:
            return None
        nid = NOTIFYICONIDENTIFIER()
        nid.cbSize = ctypes.sizeof(NOTIFYICONIDENTIFIER)
        nid.hWnd = self._hwnd
        nid.uID = 1
        r = wintypes.RECT()
        if shell32.Shell_NotifyIconGetRect(ctypes.byref(nid), ctypes.byref(r)) != 0:
            return None
        if r.right <= r.left:
            return None
        return (r.left, r.top, r.right, r.bottom)

    def destroy(self) -> None:
        if self._hwnd:
            nid = self._nid()
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            user32.PostMessageW(self._hwnd, WM_DESTROY, 0, 0)

    # -------------------------------------------------- 内部
    def _nid(self) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.hicon
        return nid

    def _run(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        cls = WNDCLASSW()
        cls.lpfnWndProc = self._wndproc_ref
        cls.hInstance = hinst
        cls.lpszClassName = "EchoTrayWnd"
        user32.RegisterClassW(ctypes.byref(cls))
        self._hwnd = user32.CreateWindowExW(0, "EchoTrayWnd", "EchoTrayHost", 0, 0, 0, 0, 0,
                                            None, None, hinst, None)
        nid = self._nid()
        nid.szTip = " "          # 有个空格占位，系统才会发悬停通知；它本身画不出气泡
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        nid.uVersion = NOTIFYICON_VERSION_4
        shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(nid))

        self._ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        # 退出消息循环后必须真正退出（避免留下僵尸进程）
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        user32.DestroyWindow(self._hwnd)

    def _menu(self) -> int:
        hmenu = user32.CreatePopupMenu()
        user32.AppendMenuW(hmenu, MF_STRING, ID_SHOW, "打开 Echo")
        user32.AppendMenuW(hmenu, MF_STRING, ID_REFRESH, "立即刷新")
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        flags = MF_STRING | (MF_CHECKED if self.autostart_getter() else 0)
        user32.AppendMenuW(hmenu, flags, ID_AUTOSTART, "开机自启")
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(hmenu, MF_STRING, ID_QUIT, "退出")
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self._hwnd)
        cmd = user32.TrackPopupMenu(hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                                    pt.x, pt.y, 0, self._hwnd, None)
        user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(hmenu)
        return int(cmd or 0)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            low = lparam & 0xFFFF
            if self.on_hover and low == NIN_POPUPOPEN:
                try:
                    self.on_hover(True)
                except Exception:
                    pass
                return 0
            if self.on_hover and low == NIN_POPUPCLOSE:
                try:
                    self.on_hover(False)
                except Exception:
                    pass
                return 0
            if low in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.on_show()
                return 0
            if low == WM_RBUTTONUP:
                cmd = self._menu()
                if cmd == ID_SHOW:
                    self.on_show()
                elif cmd == ID_REFRESH:
                    self.on_refresh()
                elif cmd == ID_AUTOSTART:
                    self.on_toggle_autostart()
                elif cmd == ID_QUIT:
                    self.on_quit()
                return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
