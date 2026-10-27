# -*- coding: utf-8 -*-
"""tools/pixcap.py —— 截图取像素的小工具（argtypes 全声明，64 位下才准）

之前的坑：GetDC/GetDIBits 不声明 restype，HANDLE 被截断成 32 位，
拿到的像素全是黑的，于是误判"卡片没变色"。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from collections import Counter

u = ctypes.WinDLL("user32", use_last_error=True)
g = ctypes.WinDLL("gdi32", use_last_error=True)

u.GetDC.restype = wintypes.HDC
u.GetDC.argtypes = [wintypes.HWND]
u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
g.CreateCompatibleDC.restype = wintypes.HDC
g.CreateCompatibleDC.argtypes = [wintypes.HDC]
g.CreateCompatibleBitmap.restype = wintypes.HANDLE
g.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
g.SelectObject.restype = wintypes.HANDLE
g.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
g.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                     ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
g.DeleteObject.argtypes = [wintypes.HANDLE]
g.DeleteDC.argtypes = [wintypes.HDC]
g.GetDIBits.argtypes = [wintypes.HDC, wintypes.HANDLE, wintypes.UINT, wintypes.UINT,
                        ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
g.GetDIBits.restype = ctypes.c_int


class BIH(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BIH), ("bmiColors", wintypes.DWORD * 3)]


def grab(x: int, y: int, w: int, h: int) -> bytes:
    """截屏幕上 (x,y,w,h) 一块，返回自顶向下的 BGRA 字节。"""
    hdc = u.GetDC(None)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    g.BitBlt(mdc, 0, 0, w, h, hdc, x, y, 0x00CC0020)      # SRCCOPY
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BIH)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(None, hdc)
    return buf.raw


def top_colors(px: bytes, step: int = 4) -> list[tuple[tuple[int, int, int], int]]:
    """最常出现的几种 (B,G,R)。"""
    c = Counter()
    for i in range(0, len(px) - 3, 4 * step):
        c[(px[i], px[i + 1], px[i + 2])] += 1
    return c.most_common(6)


def at(px: bytes, w: int, x: int, y: int) -> tuple[int, int, int]:
    i = (y * w + x) * 4
    return (px[i], px[i + 1], px[i + 2])
