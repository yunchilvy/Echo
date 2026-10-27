# -*- coding: utf-8 -*-
"""echo/core/btbatt.py —— 蓝牙设备电量

原理：Windows 蓝牙栈收到耳机/音箱通过 HFP（免提协议）上报的电量后，会写入
设备 Hands-Free / 免提节点上的未公开 PnP 属性：

    PROPERTYKEY {104EA319-6EE2-4701-BD47-8DDBF425BBE5}, 2   (BYTE, 0-100)

该属性挂在 **Hands-Free 节点**（不是主节点）上，这是大多数教程查不到电量的原因。
同一机制对蓝牙鼠标、手柄也有效。设备类型与名称来自 BTHENUM\\DEV_<地址> 节点
（Class of Device 决定是耳机 / 音箱 / 鼠标）。
"""
from __future__ import annotations

import ctypes
import re
from ctypes import wintypes
from dataclasses import dataclass

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)

DIGCF_ALLCLASSES = 0x04
DIGCF_PRESENT = 0x02
SPDRP_DEVICEDESC = 0x00
SPDRP_FRIENDLYNAME = 0x0C
ERROR_NOT_FOUND = 1168


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", GUID),
                ("DevInst", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]


setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                          wintypes.HWND, wintypes.DWORD]
setupapi.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.POINTER(SP_DEVINFO_DATA)]
setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD)]
setupapi.SetupDiGetDevicePropertyW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), ctypes.POINTER(PROPERTYKEY),
    ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.LPWSTR,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

FMTID_BLUETOOTH = GUID(0x104EA319, 0x6EE2, 0x4701,
                       (ctypes.c_ubyte * 8)(0xBD, 0x47, 0x8D, 0xDB, 0xF4, 0x25, 0xBB, 0xE5))
PID_BATTERY_LEVEL = 2
FMTID_BT_DEV = GUID(0x2BD67D8B, 0x8BEB, 0x48D5,
                    (ctypes.c_ubyte * 8)(0x87, 0xE0, 0x6C, 0xDA, 0x34, 0x28, 0x04, 0x0A))
PID_CLASS_OF_DEVICE = 4

_ADDR_RE = re.compile(r"&0&([0-9A-F]{12})_", re.I)
_ANY12_RE = re.compile(r"([0-9A-F]{12})", re.I)

# 设备名后面这些后缀对用户没有意义
_NOISE = ("hands-free ag", "hands-free", "avrcp 传输", "a2dp snk", "avrcp sink",
          "免提 ag", "免提", "hfp ag")


def device_address(instance_id: str) -> str:
    """从实例 ID 里取蓝牙地址。

    不能直接搜第一段 12 位十六进制：GUID 的最后一节（00805F9B34FB）
    也是 12 位，会先把所有设备都匹配成同一个地址。
    """
    iid = instance_id.upper()
    if "\\DEV_" in iid:
        tail = iid.split("DEV_", 1)[1]
        cand = tail[:12]
        if _ANY12_RE.fullmatch(cand):
            return cand
    m = _ADDR_RE.search(iid)
    if m:
        return m.group(1).upper()
    all12 = _ANY12_RE.findall(iid)
    return all12[-1].upper() if all12 else ""


def clean_name(name: str) -> str:
    raw = (name or "").strip()
    low = raw.lower()
    for noise in _NOISE:
        idx = low.rfind(noise)
        if idx > 0:
            raw = raw[:idx].strip(" -·|,")
            break
    return raw.strip() or name


@dataclass
class BtDevice:
    address: str
    name: str
    battery: int | None = None
    connected: bool = False
    kind: str = "earbuds"
    detail: str = ""


def _iid(hdev, info) -> str:
    buf = ctypes.create_unicode_buffer(512)
    need = wintypes.DWORD(0)
    if not setupapi.SetupDiGetDeviceInstanceIdW(hdev, ctypes.byref(info), buf, 512,
                                                ctypes.byref(need)):
        return ""
    return buf.value


def _strprop(hdev, info, prop: int) -> str:
    buf = ctypes.create_string_buffer(1024)
    need = wintypes.DWORD(0)
    if not setupapi.SetupDiGetDeviceRegistryPropertyW(
            hdev, ctypes.byref(info), prop, None, buf, 1024, ctypes.byref(need)):
        return ""
    return buf.raw.decode("utf-16-le", errors="ignore").split("\x00")[0].strip()


def _battery(hdev, info) -> int | None:
    key = PROPERTYKEY(FMTID_BLUETOOTH, PID_BATTERY_LEVEL)
    ptype = wintypes.ULONG(0)
    value = ctypes.c_ubyte(0)
    need = wintypes.DWORD(0)
    ok = setupapi.SetupDiGetDevicePropertyW(
        hdev, ctypes.byref(info), ctypes.byref(key), ctypes.byref(ptype),
        ctypes.byref(value), 1, ctypes.byref(need), 0)
    if not ok:
        return None
    return min(100, int(value.value))


def _cod(hdev, info) -> int:
    key = PROPERTYKEY(FMTID_BT_DEV, PID_CLASS_OF_DEVICE)
    ptype = wintypes.ULONG(0)
    value = wintypes.DWORD(0)
    need = wintypes.DWORD(0)
    ok = setupapi.SetupDiGetDevicePropertyW(
        hdev, ctypes.byref(info), ctypes.byref(key), ctypes.byref(ptype),
        ctypes.byref(value), 4, ctypes.byref(need), 0)
    return int(value.value) if ok else 0


def cod_kind(cod: int) -> str:
    """Class of Device → 设备类型。"""
    if not cod:
        return ""
    major = (cod >> 8) & 0x1F
    minor = (cod >> 2) & 0x3F
    if major == 0x05:                       # Peripheral
        if minor in (0x01, 0x02):           # 键盘 / 指点设备
            return "mouse"
        if minor == 0x03:
            return "gamepad"
        return ""
    if major != 0x04:                       # 非 Audio/Video
        return "other"
    return {
        0x01: "earbuds",    # Wearable Headset Device
        0x02: "headset",    # Hands-free Device
        0x03: "headset",    # Microphone
        0x04: "speaker",    # Loudspeaker
        0x05: "speaker",    # Loudspeaker
        0x06: "headphones", # Headphones
        0x07: "speaker",    # Portable Audio
    }.get(minor, "headset")


def _enum(flags: int = DIGCF_PRESENT | DIGCF_ALLCLASSES):
    hdev = setupapi.SetupDiGetClassDevsW(None, None, None, flags)
    if not hdev:
        return
    try:
        idx = 0
        while True:
            info = SP_DEVINFO_DATA()
            info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            if not setupapi.SetupDiEnumDeviceInfo(hdev, idx, ctypes.byref(info)):
                return
            idx += 1
            yield hdev, info
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)


def read_bluetooth() -> list[BtDevice]:
    """读取所有能拿到电量的蓝牙设备。"""
    names: dict[str, str] = {}
    kinds: dict[str, str] = {}
    for hdev, info in _enum():
        iid = _iid(hdev, info)
        if not iid.upper().startswith("BTHENUM\\DEV_"):
            continue
        addr = device_address(iid)
        if not addr:
            continue
        name = _strprop(hdev, info, SPDRP_FRIENDLYNAME) or _strprop(hdev, info, SPDRP_DEVICEDESC)
        if name:
            names[addr] = clean_name(name)
        k = cod_kind(_cod(hdev, info))
        if k:
            kinds[addr] = k

    found: dict[str, BtDevice] = {}
    for hdev, info in _enum():
        iid = _iid(hdev, info)
        if not iid.upper().startswith("BTHENUM"):
            continue
        addr = device_address(iid)
        if not addr:
            continue
        level = _battery(hdev, info)
        if level is None:
            continue
        prev = found.get(addr)
        if prev is not None and prev.battery:
            continue
        name = names.get(addr) or clean_name(
            _strprop(hdev, info, SPDRP_FRIENDLYNAME) or "") or addr
        found[addr] = BtDevice(
            address=addr, name=name, battery=level,
            connected=level > 0,
            kind=kinds.get(addr) or "headset",
            detail="来自蓝牙 HFP 电量上报",
        )
    return list(found.values())
