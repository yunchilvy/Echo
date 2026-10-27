# -*- coding: utf-8 -*-
"""echo/core/winicon.py —— 纯 Python 画出 Echo 的标记（不依赖 Pillow）

标记＝一个环 + 环内一段 1/4 圆弧 + 圆心一颗点：向外扩散的一圈回声，
中心的点既是"信号源"也是电量指示。墨色圆角方砖，米白描边，中心点用琥珀色。

实现：距离场光栅器。每个像素算到圆/圆弧/点的最短距离，
在描边边界做 1px 羽化做抗锯齿，比超采样快一个量级。
"""
from __future__ import annotations

import math
import os
import struct
import zlib

INK = (0x12, 0x11, 0x10)
CREAM = (0xF6, 0xF3, 0xEE)
AMBER = (0xE5, 0xB3, 0x6A)

# 24 网格里的几何：同心三段（外环 / 1/4 弧 / 圆点），半径按外环等比缩放
R_RATIO = (1.0, 0.61, 0.276)      # 外环 · 弧 · 点
ARC_SPAN = 90.0                   # 弧只画一个象限（左上）
# 小尺寸要加粗、放大、砍细节，否则 16px 会糊成一团
STYLE = {  # size 上限: (描边宽度u, 铺满直径u, 画弧)
    16: (2.3, 23.0, True),
    20: (2.2, 22.0, True),
    32: (2.1, 21.0, True),
    9999: (1.9, 20.4, True),
}
ICON_VERSION = 4


def _style(size: int):
    for cap in sorted(STYLE):
        if size <= cap:
            return STYLE[cap]
    return STYLE[9999]


# ------------------------------------------------------------------ 光栅
def _stroke(d, half, feather=1.0):
    """到描边中线的距离 → 覆盖率。"""
    x = (half + feather * 0.5 - d) / feather
    return 0.0 if x <= 0.0 else 1.0 if x >= 1.0 else x


def _fill(sd, feather=1.0):
    """有符号距离（内部为正）→ 覆盖率。"""
    x = (sd + feather * 0.5) / feather
    return 0.0 if x <= 0.0 else 1.0 if x >= 1.0 else x


def render(size: int, bg: tuple | None = INK, corner: float = 0.26) -> bytes:
    """生成 size×size RGBA。bg=None 时只画标记（透明底）。"""
    buf = bytearray(size * size * 4)
    s = float(size)
    w_u, d_u, with_arc = _style(size)
    k = s * d_u / 24.0                 # 24 网格 → 像素
    half = w_u * k / 24.0 / 2.0
    cx = cy = s / 2.0
    r_out = R_RATIO[0] * (d_u / 2.0 - w_u / 2.0) * k / 24.0
    r_arc = r_out * R_RATIO[1]
    r_dot = r_out * R_RATIO[2]
    rad = s * corner
    # 弧的角度窗口：左上象限，两端各留半个线宽，看起来像平头笔触
    pad = math.degrees(half / max(r_arc, 0.001)) + 2.0

    for y in range(size):
        py = y + 0.5 - cy
        for x in range(size):
            px = x + 0.5 - cx
            r = g = b = 0.0
            a = 0.0
            if bg is not None:                       # 圆角方砖
                rad_c = rad
                qx = rad_c if x + 0.5 < rad_c else s - rad_c if x + 0.5 > s - rad_c else x + 0.5
                qy = rad_c if y + 0.5 < rad_c else s - rad_c if y + 0.5 > s - rad_c else y + 0.5
                ca = _fill(rad_c - math.hypot(x + 0.5 - qx, y + 0.5 - qy))
                if ca > 0:
                    r, g, b, a = bg[0] * ca / 255.0, bg[1] * ca / 255.0, bg[2] * ca / 255.0, ca
            dist = math.hypot(px, py)
            # 外环
            c_ring = _stroke(abs(dist - r_out), half)
            # 1/4 弧
            c_arc = 0.0
            if with_arc:
                ang = math.degrees(math.atan2(py, px))      # 屏幕坐标：-180..180，向上为负
                if -180.0 - pad <= ang <= -90.0 + pad:
                    c_arc = _stroke(abs(dist - r_arc), half)
            # 中心点
            c_dot = _stroke(abs(dist - r_dot), half)
            if c_dot > 0.55 and r_dot - half < 1.2:         # 小尺寸时点太小，直接填实
                c_dot = 1.0
            for c_mark, col in ((c_ring, CREAM), (c_arc, CREAM), (c_dot, AMBER)):
                if c_mark <= 0:
                    continue
                r = r * (1 - c_mark) + col[0] * c_mark
                g = g * (1 - c_mark) + col[1] * c_mark
                b = b * (1 - c_mark) + col[2] * c_mark
                a = a + c_mark * (1 - a)
            if a <= 0.002:
                continue
            i = (y * size + x) * 4
            buf[i] = int(r / a)
            buf[i + 1] = int(g / a)
            buf[i + 2] = int(b / a)
            buf[i + 3] = int(a * 255)
    return bytes(buf)


def _png(size: int, rgba: bytes) -> bytes:
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        raw += rgba[y * size * 4:(y + 1) * size * 4]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def write_png(path: str, size: int = 256, bg=INK) -> str:
    data = _png(size, render(size, bg=INK if bg else None))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def write_ico(path: str, sizes=(256, 64, 48, 32, 20, 16)) -> None:
    frames = [(s, _png(s, render(s, bg=INK))) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries, blobs = b"", b""
    for s, data in frames:
        entries += struct.pack("<BBBBHHII", 0 if s >= 256 else s,
                               0 if s >= 256 else s, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(header + entries + blobs)


def ensure_ico(path: str) -> None:
    """只在第一次（或换版）生成，别每次启动都算一遍光栅。"""
    stamp = path + f".v{ICON_VERSION}"
    if os.path.exists(path) and os.path.exists(stamp):
        return
    try:
        write_ico(path)
        with open(stamp, "w") as f:
            f.write("1")
    except Exception:
        pass


def tray_hicon(size: int = 32):
    """托盘用 HICON（带墨色方砖，深浅任务栏都看得见）。"""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    rgba = render(size, bg=INK)
    bgra = bytearray()
    for i in range(0, len(rgba), 4):
        bgra += bytes((rgba[i + 2], rgba[i + 1], rgba[i], rgba[i + 3]))

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = size
    bmi.bmiHeader.biHeight = -size          # 自上而下
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0

    bits = ctypes.c_void_p()
    hdc = user32.GetDC(None)
    hbmp = gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    ctypes.memmove(bits, bytes(bgra), len(bgra))
    user32.ReleaseDC(None, hdc)

    mask_bits = ctypes.create_string_buffer(((size + 31) // 32) * 4 * size)
    hbmp_mask = gdi32.CreateBitmap(size, size, 1, 1, mask_bits)

    class ICONINFO(ctypes.Structure):
        _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                    ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HANDLE),
                    ("hbmColor", wintypes.HANDLE)]

    user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
    user32.CreateIconIndirect.restype = wintypes.HANDLE
    ii = ICONINFO(True, 0, 0, hbmp_mask, hbmp)
    hicon = user32.CreateIconIndirect(ctypes.byref(ii))
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteObject(hbmp_mask)
    return hicon


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "..", "build", "echo.ico")
    write_ico(out)
    write_png(os.path.join(os.path.dirname(__file__), "..", "build", "echo.png"), 256)
    print("wrote", os.path.abspath(out))
