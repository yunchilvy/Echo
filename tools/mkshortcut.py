# -*- coding: utf-8 -*-
"""tools/mkshortcut.py —— 用 ShellLink 的 COM 接口建 .lnk（不依赖 WScript.Shell）

    python tools/mkshortcut.py <快捷方式路径> <目标exe> [工作目录] [描述]
"""
import ctypes
import sys
from ctypes import wintypes

ole32 = ctypes.WinDLL("ole32")
ole32.CoInitialize(None)

CLSID_ShellLink = "{00021401-0000-0000-C000-000000000046}"
IID_IShellLinkW = "{000214F9-0000-0000-C000-000000000046}"
IID_IPersistFile = "{0000010B-0000-0000-C000-000000000046}"


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


def guid(s: str) -> GUID:
    s = s.strip("{}")
    a, b, c, d1, d2 = s.split("-")
    d = d1 + d2
    g = GUID()
    g.Data1 = int(a, 16)
    g.Data2 = int(b, 16)
    g.Data3 = int(c, 16)
    g.Data4 = (ctypes.c_ubyte * 8)(*bytes.fromhex(d))
    return g


def vt(ptr, index):
    """取 COM 接口的第 index 个虚函数。"""
    vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.cast(vtable[index], ctypes.c_void_p).value


def call(ptr, index, restype, *argtypes):
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(vt(ptr, index))


def main() -> int:
    lnk, target = sys.argv[1], sys.argv[2]
    workdir = sys.argv[3] if len(sys.argv) > 3 else None
    desc = sys.argv[4] if len(sys.argv) > 4 else ""

    clsid, iid_link, iid_persist = guid(CLSID_ShellLink), guid(IID_IShellLinkW), guid(IID_IPersistFile)
    p = ctypes.c_void_p()
    ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid_link),
                           ctypes.byref(p))
    if not p:
        print("CoCreateInstance 失败")
        return 1

    set_path = call(p, 20, ctypes.c_long, wintypes.LPCWSTR, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint)
    set_work = call(p, 9, ctypes.c_long, wintypes.LPCWSTR)
    set_desc = call(p, 7, ctypes.c_long, wintypes.LPCWSTR)
    set_icon = call(p, 17, ctypes.c_long, wintypes.LPCWSTR, ctypes.c_int)

    set_path(p, target, 1, None, 0)
    if workdir:
        set_work(p, workdir)
    if desc:
        set_desc(p, desc)
    set_icon(p, target, 0)

    q = ctypes.c_void_p()
    p_ptr = ctypes.cast(p, ctypes.POINTER(ctypes.c_void_p))
    hr = call(p, 0, ctypes.c_long, ctypes.POINTER(GUID), ctypes.c_void_p)(p, ctypes.byref(iid_persist), ctypes.byref(q))
    if hr != 0 or not q:
        print("QueryInterface(IPersistFile) 失败 hr=%s" % hr)
        return 1
    save = call(q, 6, ctypes.c_long, wintypes.LPCWSTR, wintypes.BOOL)
    hr = save(q, lnk, True)
    print("保存", lnk, "hr=%s" % hr)
    return 0 if hr == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
