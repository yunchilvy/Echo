# -*- coding: utf-8 -*-
"""tools/shot.py —— 截取 Echo 窗口（Win32 BitBlt，纯 ctypes + 自带 PNG 编码）"""
import ctypes
import os
import sys
from ctypes import wintypes

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from core.winicon import _png            # noqa: E402

try:                       # 先声明 DPI 感知，否则取到的窗口坐标是虚拟化后的
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def shot(title: str, out: str) -> bool:
    # 同名窗口不止一个（托盘隐藏窗也叫 Echo），取面积最大的可见窗口
    best = [None]
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _):
        n = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(h, n, 256)
        if n.value != title:
            return True
        r = RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        area = (r.right - r.left) * (r.bottom - r.top)
        if best[0] is None or area > best[0][0]:
            best[0] = (area, h)
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    hwnd = best[0][1] if best[0] else None
    if not hwnd:
        print("窗口未找到:", title)
        return False
    user32.SetForegroundWindow(hwnd)
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = user32.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mdc, bmp)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    mode = os.environ.get("SHOT_MODE", "print")
    if mode == "screen":
        # 从桌面 DC 直接拷屏，最接近人眼看到的画面
        user32.SetForegroundWindow(hwnd)
        import time as _t
        _t.sleep(0.35)
        desk = user32.GetDC(None)
        ok = gdi32.BitBlt(mdc, 0, 0, w, h, desk, r.left, r.top, 0x00CC0020)
        user32.ReleaseDC(None, desk)
        if not ok:
            print("BitBlt failed")
    elif not user32.PrintWindow(hwnd, mdc, 2):
        gdi32.BitBlt(mdc, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)

    raw = bytearray(buf.raw)
    for i in range(0, len(raw), 4):
        raw[i], raw[i + 2] = raw[i + 2], raw[i]
        if raw[i + 3] == 0:
            raw[i + 3] = 255
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "wb") as f:
        f.write(_png(w, bytes(raw)))
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(hwnd, hdc)
    print("saved", out, w, "x", h)
    return True


if __name__ == "__main__":
    title = sys.argv[1] if len(sys.argv) > 1 else "Echo"
    out = sys.argv[2] if len(sys.argv) > 2 else "shots/echo.png"
    sys.exit(0 if shot(title, out) else 1)
