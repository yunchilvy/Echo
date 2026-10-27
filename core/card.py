# -*- coding: utf-8 -*-
"""echo/core/card.py —— 托盘悬停时弹出的原生小卡片

为什么不用第二个 WebView：跑一个隐藏的 WebView2 会连带把主窗口的输入路由搞坏
（实测鼠标点哪儿都没反应），而且隐藏→显示必然白一帧。这里只用 GDI 画：
一个 WS_POPUP + WS_EX_TOOLWINDOW 的小窗，双缓冲，圆角用 window region 裁，
字体走系统 ClearType。配色跟随主界面的深色 / 浅色主题。

坐标与尺寸全部是物理像素；尺寸只由 size() 算，调用方拿同一个数定位，
避免"窗口宽 178 却按 264 居中"这种错位。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wintypes.HDC), ("fErase", wintypes.BOOL),
                ("rcPaint", wintypes.RECT), ("fRestore", wintypes.BOOL),
                ("fIncUpdate", wintypes.BOOL), ("rgbReserved", ctypes.c_byte * 32)]


user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                   wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.BeginPaint.restype = wintypes.HDC
user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
user32.UpdateWindow.argtypes = [wintypes.HWND]
user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                             ctypes.POINTER(wintypes.RECT), wintypes.UINT]
user32.DrawTextW.restype = ctypes.c_int
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HANDLE]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]

gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.restype = wintypes.HANDLE
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wintypes.HANDLE
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                         wintypes.DWORD]
gdi32.CreateSolidBrush.restype = wintypes.HANDLE
gdi32.CreateSolidBrush.argtypes = [wintypes.DWORD]
gdi32.CreatePen.restype = wintypes.HANDLE
gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.CreateFontW.restype = wintypes.HANDLE
gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR]
gdi32.CreateRoundRectRgn.restype = wintypes.HANDLE
gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.DWORD]
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.GetStockObject.restype = wintypes.HANDLE
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.RoundRect.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.MoveToEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
gdi32.LineTo.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]

WS_POPUP, SW_SHOWNOACTIVATE, SW_HIDE = 0x80000000, 4, 0
WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_NOACTIVATE = 0x80, 0x08, 0x08000000
DT_LEFT, DT_RIGHT, DT_VCENTER = 0x0000, 0x0002, 0x0004
DT_SINGLELINE, DT_END_ELLIPSIS = 0x0020, 0x8000
# GetStockObject：0=WHITE_BRUSH 1=LTGRAY 2=GRAY 3=DKGRAY 4=BLACK 5=NULL(空心)
NULL_BRUSH, PS_SOLID, TRANSPARENT = 5, 0, 1
SRCCOPY = 0x00CC0020

# 尺寸（逻辑像素）
W, PAD, HEAD_H, ROW_H, RADIUS = 178, 11, 21, 24, 12
TOP_PAD, BOT_PAD = 9, 9

# 两套配色，直接取自 ui/index.html 的 tokens
PALETTE = {
    "dark": {
        "bg": 0x181717,        # --surface-2 #171718
        "border": 0x2E2B2A,    # --line-strong
        "line": 0x262524,      # --line
        "text": 0xECF1F4,      # --text #f4f1ec
        "dim": 0x868D91,       # --text-2 #918d86
        "ok": 0x9FCA6F,        # --ok #6fca9f
        "warn": 0x6AB3E5,      # --accent #e5b36a
        "low": 0x5F70E8,       # --danger #e8705f
    },
    "light": {
        "bg": 0xFBFFFE,        # --surface #fffefb
        "border": 0xD0D8DD,    # --line-strong
        "line": 0xDAE1E5,      # --line
        "text": 0x141617,      # --text #171614
        "dim": 0x626A6F,       # --text-2 #6f6a62
        "ok": 0x688F2F,        # --ok #2f8f68
        "warn": 0x1A72B0,      # --accent #b0721a
        "low": 0x3C50C5,       # --danger #c5503c
    },
}


def size(n: int, k: float) -> tuple[int, int]:
    """卡片在屏幕上的物理尺寸。定位和建窗都用它，保证居中对得上图标。"""
    n = max(1, min(6, n))
    return (int(W * k), int((TOP_PAD + HEAD_H + ROW_H * n + BOT_PAD) * k))


class HoverCard:
    def __init__(self):
        self._hwnd = None
        self._rows: list[tuple[str, str, str]] = []   # (名字, 数值, 档位 key)
        self._head = ""
        self._w = self._h = 0
        self._k = 1.0
        self._theme = "dark"
        self._proc = WNDPROC(self._wndproc)
        self._create()

    # -------------------------------------------------- 建窗
    def _create(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        cls = WNDCLASSW()
        cls.lpfnWndProc = self._proc
        cls.hInstance = hinst
        cls.lpszClassName = "EchoCardWnd"
        cls.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32512))  # IDC_ARROW
        user32.RegisterClassW(ctypes.byref(cls))
        # TOOLWINDOW：不进任务栏、不参与 Alt+Tab；NOACTIVATE：弹出来不抢焦点。
        # 少这两个样式，卡片每次弹出都会在任务栏闪一个图标。
        self._hwnd = user32.CreateWindowExW(
            WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE,
            "EchoCardWnd", "EchoCard", WS_POPUP,
            -4000, -4000, 10, 10, None, None, hinst, None)

    def _c(self, key: str) -> int:
        return PALETTE.get(self._theme, PALETTE["dark"])[key]

    # -------------------------------------------------- 绘制
    def _font(self, px: float, weight: int) -> int:
        h = -max(9, int(round(px * self._k)))
        return gdi32.CreateFontW(h, 0, 0, 0, weight, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI")

    def _paint(self) -> None:
        w, h = self._w, self._h
        hdc = user32.GetDC(self._hwnd)
        mdc = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        gdi32.SelectObject(mdc, bmp)
        self._draw(mdc)
        gdi32.BitBlt(hdc, 0, 0, w, h, mdc, 0, 0, SRCCOPY)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(self._hwnd, hdc)

    def _draw(self, mdc) -> None:
        """把整张卡片画进给定 DC（物理尺寸 self._w × self._h）。"""
        w, h, k = self._w, self._h, self._k

        bg = gdi32.CreateSolidBrush(self._c("bg"))
        rc = wintypes.RECT(0, 0, w, h)
        user32.FillRect(mdc, ctypes.byref(rc), bg)
        gdi32.DeleteObject(bg)

        pen = gdi32.CreatePen(PS_SOLID, max(1, int(k)), self._c("border"))
        old_pen = gdi32.SelectObject(mdc, pen)
        old_brush = gdi32.SelectObject(mdc, gdi32.GetStockObject(NULL_BRUSH))
        gdi32.RoundRect(mdc, 0, 0, w, h, int(RADIUS * k * 2), int(RADIUS * k * 2))
        gdi32.SelectObject(mdc, old_brush)
        gdi32.SelectObject(mdc, old_pen)
        gdi32.DeleteObject(pen)

        gdi32.SetBkMode(mdc, TRANSPARENT)
        f_head = self._font(10.5, 500)
        f_name = self._font(12.5, 400)
        f_val = self._font(13, 600)

        of = gdi32.SelectObject(mdc, f_head)
        gdi32.SetTextColor(mdc, self._c("dim"))
        r = wintypes.RECT(int(PAD * k), int(TOP_PAD * k),
                          w - int(PAD * k), int((TOP_PAD + 16) * k))
        user32.DrawTextW(mdc, self._head, -1, ctypes.byref(r),
                         DT_LEFT | DT_SINGLELINE | DT_END_ELLIPSIS)
        gdi32.SelectObject(mdc, of)
        gdi32.DeleteObject(f_head)

        y = int((TOP_PAD + HEAD_H) * k)
        for i, (name, val, band) in enumerate(self._rows):
            if i:
                lp = gdi32.CreatePen(PS_SOLID, 1, self._c("line"))
                op = gdi32.SelectObject(mdc, lp)
                gdi32.MoveToEx(mdc, int(PAD * k), y, None)
                gdi32.LineTo(mdc, w - int(PAD * k), y)
                gdi32.SelectObject(mdc, op)
                gdi32.DeleteObject(lp)
            ry = y + int(3 * k)
            rn = wintypes.RECT(int(PAD * k), ry, w - int(PAD * k) - int(52 * k),
                               ry + int(ROW_H * k))
            ofn = gdi32.SelectObject(mdc, f_name)
            gdi32.SetTextColor(mdc, self._c("text"))
            user32.DrawTextW(mdc, name, -1, ctypes.byref(rn),
                             DT_LEFT | DT_SINGLELINE | DT_END_ELLIPSIS | DT_VCENTER)
            gdi32.SelectObject(mdc, ofn)

            rv = wintypes.RECT(w - int(PAD * k) - int(56 * k), ry,
                               w - int(PAD * k), ry + int(ROW_H * k))
            ofv = gdi32.SelectObject(mdc, f_val)
            gdi32.SetTextColor(mdc, self._c(band))
            user32.DrawTextW(mdc, val, -1, ctypes.byref(rv),
                             DT_RIGHT | DT_SINGLELINE | DT_VCENTER)
            gdi32.SelectObject(mdc, ofv)
            y += int(ROW_H * k)

        for f in (f_name, f_val):
            gdi32.DeleteObject(f)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == 0x000F:                       # WM_PAINT
            ps = PAINTSTRUCT()
            user32.BeginPaint(hwnd, ctypes.byref(ps))
            try:
                self._paint()
            finally:
                user32.EndPaint(hwnd, ctypes.byref(ps))
            return 0
        if msg == 0x0014:                       # WM_ERASEBKGND：自己画，别闪
            return 1
        if msg == 0x0021:                       # WM_MOUSEACTIVATE
            return 3                            # MA_NOACTIVATE
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # -------------------------------------------------- 对外
    def show(self, rows, head: str, x: int, y: int, k: float = 1.0,
             theme: str = "dark") -> None:
        """rows: [(名字, 数值文本, 档位 'ok'|'warn'|'low')]；x/y/k 为物理像素与缩放比。

        x/y 由调用方用 size() 算出，这里不再自己推算位置 —— 之前两边各算一套
        宽度，卡片就会整体偏左。
        """
        self._rows = list(rows)[:6]
        self._head = head
        self._k = max(1.0, k)
        self._theme = "light" if str(theme) == "light" else "dark"
        self._w, self._h = size(len(self._rows), self._k)
        rgn = gdi32.CreateRoundRectRgn(0, 0, self._w + 1, self._h + 1,
                                       int(RADIUS * self._k * 2),
                                       int(RADIUS * self._k * 2))
        user32.SetWindowRgn(self._hwnd, rgn, True)
        user32.SetWindowPos(self._hwnd, wintypes.HWND(-1), x, y,
                            self._w, self._h, 0x0010)      # SWP_NOACTIVATE
        user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE)
        user32.InvalidateRect(self._hwnd, None, True)
        user32.UpdateWindow(self._hwnd)

    def hide(self) -> None:
        if self._hwnd:
            user32.ShowWindow(self._hwnd, SW_HIDE)

    @property
    def hwnd(self):
        return self._hwnd
