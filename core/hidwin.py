# -*- coding: utf-8 -*-
"""echo/core/hidwin.py —— Windows HID 访问层（纯 ctypes，零第三方依赖）

只调用 setupapi / hid / kernel32。实现思路与踩坑处理参考了
github.com/yszyszysz233/2.4G-battery-viewer 的公开说明，代码为 Echo 重写。

关键点：
  * 鼠标/键盘等系统独占的顶层集合只能用 dwDesiredAccess=0 打开，
    此时仍可读属性、能力表和 Feature Report。
  * 绝不在同一句柄上"阻塞读 + 并发写"。收 Input Report 用独立的
    FILE_FLAG_OVERLAPPED 读句柄 + 事件超时 + CancelIoEx。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Optional

setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
FILE_SHARE_READ = 0x01
FILE_SHARE_WRITE = 0x02
OPEN_EXISTING = 3
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0
HIDP_STATUS_SUCCESS = 0x00110000

HidP_Input, HidP_Output, HidP_Feature = 0, 1, 2

# 描述符里"电量"相关的标准 usage
BATTERY_USAGES = {
    (0x0006, 0x0020): "Battery Strength",
    (0x000D, 0x003B): "Battery Strength (digitizer)",
    (0x0085, 0x0001): "Absolute State of Charge",
    (0x0085, 0x0066): "Relative State of Charge",
    (0x0085, 0x0065): "Battery Level",
}

SYSTEM_RESERVED_USAGES = {
    (0x0001, 0x0001), (0x0001, 0x0002), (0x0001, 0x0006), (0x0001, 0x0007),
    (0x000D, 0x0001), (0x000D, 0x0002), (0x000D, 0x0004), (0x000D, 0x0005),
}


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                ("hEvent", ctypes.c_void_p)]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", wintypes.USHORT),
                ("ProductID", wintypes.USHORT), ("VersionNumber", wintypes.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT), ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT)]


class _RANGE(ctypes.Structure):
    _fields_ = [("UsageMin", wintypes.USHORT), ("UsageMax", wintypes.USHORT)]


class _NOT_RANGE(ctypes.Structure):
    _fields_ = [("Usage", wintypes.USHORT), ("Reserved1", wintypes.USHORT),
                ("StringIndex", wintypes.USHORT), ("Reserved2", wintypes.USHORT),
                ("DesignatorIndex", wintypes.USHORT), ("Reserved3", wintypes.USHORT),
                ("DataIndex", wintypes.USHORT), ("Reserved4", wintypes.USHORT)]


class _USAGE_UNION(ctypes.Union):
    _fields_ = [("Range", _RANGE), ("NotRange", _NOT_RANGE)]


class HIDP_VALUE_CAPS(ctypes.Structure):
    _fields_ = [
        ("UsagePage", wintypes.USHORT), ("ReportID", ctypes.c_ubyte),
        ("IsAlias", ctypes.c_ubyte), ("BitField", wintypes.USHORT),
        ("LinkCollection", wintypes.USHORT), ("LinkUsage", wintypes.USHORT),
        ("LinkUsagePage", wintypes.USHORT), ("IsRange", ctypes.c_ubyte),
        ("IsStringRange", ctypes.c_ubyte), ("IsDesignatorRange", ctypes.c_ubyte),
        ("IsAbsolute", ctypes.c_ubyte), ("HasNull", ctypes.c_ubyte),
        ("Reserved", ctypes.c_ubyte), ("BitSize", wintypes.USHORT),
        ("ReportCount", wintypes.USHORT), ("Reserved2", wintypes.USHORT * 5),
        ("UnitsExp", wintypes.ULONG), ("Units", wintypes.ULONG),
        ("LogicalMin", wintypes.LONG), ("LogicalMax", wintypes.LONG),
        ("PhysicalMin", wintypes.LONG), ("PhysicalMax", wintypes.LONG),
        ("u", _USAGE_UNION)]


setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR,
                                          wintypes.HWND, wintypes.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID), wintypes.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.c_void_p]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
kernel32.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
kernel32.CreateEventW.restype = ctypes.c_void_p
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL,
                                  wintypes.LPCWSTR]
kernel32.ResetEvent.argtypes = [ctypes.c_void_p]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
kernel32.GetOverlappedResult.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
kernel32.CancelIoEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

hid.HidD_GetAttributes.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.restype = ctypes.c_long
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidP_GetValueCaps.restype = ctypes.c_long
hid.HidP_GetValueCaps.argtypes = [ctypes.c_int, ctypes.c_void_p,
                                  ctypes.POINTER(wintypes.USHORT), ctypes.c_void_p]
hid.HidP_GetUsageValue.restype = ctypes.c_long
hid.HidP_GetUsageValue.argtypes = [
    ctypes.c_int, wintypes.USHORT, wintypes.USHORT, wintypes.USHORT,
    ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_GetFeature.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_SetFeature.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_SetOutputReport.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG]
for _fn in ("HidD_GetProductString", "HidD_GetManufacturerString",
            "HidD_GetSerialNumberString"):
    _f = getattr(hid, _fn)
    _f.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG]

HID_GUID = GUID(0x4D1E55B2, 0xF16F, 0x11CF,
                (ctypes.c_ubyte * 8)(0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30))


def _wide(func, handle, size: int = 512) -> str:
    buf = ctypes.create_string_buffer(size)
    try:
        if not func(handle, buf, size):
            return ""
    except Exception:
        return ""
    return buf.raw.decode("utf-16-le", errors="ignore").split("\x00")[0].strip()


def _parse_vid_pid(path: str) -> tuple[int, int]:
    low = path.lower()
    vid = pid = 0
    if "vid_" in low:
        try:
            vid = int(low.split("vid_")[1][:4], 16)
        except Exception:
            pass
    if "pid_" in low:
        try:
            pid = int(low.split("pid_")[1][:4], 16)
        except Exception:
            pass
    return vid, pid


def physical_group_key(path: str) -> str:
    """同一物理接收器的多个接口/集合归为一组（去掉 mi_ 与 col 段）。"""
    parts = path.split("#")
    if len(parts) < 2:
        return path
    keep = [p for p in parts[1].split("&")
            if not (p.lower().startswith("col") or p.lower().startswith("mi_"))]
    return "&".join(keep) or parts[1]


@dataclass
class HidInterface:
    path: str
    vid: int = 0
    pid: int = 0
    version: int = 0
    usage_page: int = 0
    usage: int = 0
    input_len: int = 0
    output_len: int = 0
    feature_len: int = 0
    product: str = ""
    manufacturer: str = ""
    serial: str = ""
    error: str = ""
    group: str = ""

    @property
    def vid_pid(self) -> str:
        return f"{self.vid:04X}:{self.pid:04X}"

    @property
    def reserved(self) -> bool:
        return (self.usage_page, self.usage) in SYSTEM_RESERVED_USAGES

    @property
    def is_vendor_page(self) -> bool:
        return self.usage_page >= 0xFF00

    @property
    def usage_text(self) -> str:
        return f"0x{self.usage_page:04X}:0x{self.usage:04X}"


def enumerate_hid() -> list[HidInterface]:
    out: list[HidInterface] = []
    hdev = setupapi.SetupDiGetClassDevsW(ctypes.byref(HID_GUID), None, None,
                                        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if not hdev or hdev == INVALID_HANDLE_VALUE:
        return out
    try:
        idx = 0
        while True:
            iface = SP_DEVICE_INTERFACE_DATA()
            iface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                    hdev, None, ctypes.byref(HID_GUID), idx, ctypes.byref(iface)):
                break
            idx += 1
            need = wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(iface), None, 0, ctypes.byref(need), None)
            if not need.value:
                continue
            buf = ctypes.create_string_buffer(need.value)
            ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = 8
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                    hdev, ctypes.byref(iface), buf, need.value, None, None):
                continue
            path = ctypes.cast(ctypes.addressof(buf) + 4, ctypes.c_wchar_p).value or ""
            if not path:
                continue
            item = _probe(path)
            item.group = physical_group_key(path)
            out.append(item)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return out


def _probe(path: str) -> HidInterface:
    item = HidInterface(path=path)
    item.vid, item.pid = _parse_vid_pid(path)
    h = kernel32.CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                             OPEN_EXISTING, 0, None)
    if not h or h == INVALID_HANDLE_VALUE:
        item.error = f"无法打开({ctypes.get_last_error()})"
        return item
    try:
        attrs = HIDD_ATTRIBUTES()
        attrs.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
        if hid.HidD_GetAttributes(h, ctypes.byref(attrs)):
            item.vid, item.pid, item.version = (attrs.VendorID, attrs.ProductID,
                                                attrs.VersionNumber)
        item.product = _wide(hid.HidD_GetProductString, h)
        item.manufacturer = _wide(hid.HidD_GetManufacturerString, h)
        item.serial = _wide(hid.HidD_GetSerialNumberString, h)
        pp = ctypes.c_void_p()
        if hid.HidD_GetPreparsedData(h, ctypes.byref(pp)) and pp:
            try:
                caps = HIDP_CAPS()
                if hid.HidP_GetCaps(pp, ctypes.byref(caps)) == HIDP_STATUS_SUCCESS:
                    item.usage_page, item.usage = caps.UsagePage, caps.Usage
                    item.input_len = caps.InputReportByteLength
                    item.output_len = caps.OutputReportByteLength
                    item.feature_len = caps.FeatureReportByteLength
            finally:
                hid.HidD_FreePreparsedData(pp)
    finally:
        kernel32.CloseHandle(h)
    return item


class HidError(OSError):
    pass


class HidDevice:
    """已打开的 HID 接口句柄。write=True 时尝试读写权限，失败自动降级为只读。"""

    def __init__(self, path: str, write: bool = False):
        self.path = path
        access = (GENERIC_READ | GENERIC_WRITE) if write else 0
        h = kernel32.CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                 None, OPEN_EXISTING, 0, None)
        self.write_access = write
        if (not h or h == INVALID_HANDLE_VALUE) and write:
            h = kernel32.CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                     None, OPEN_EXISTING, 0, None)
            self.write_access = False
        if not h or h == INVALID_HANDLE_VALUE:
            raise HidError(f"CreateFileW 失败(err={ctypes.get_last_error()})")
        self.handle = h
        self._pp = ctypes.c_void_p()
        if not hid.HidD_GetPreparsedData(self.handle, ctypes.byref(self._pp)):
            self._pp = ctypes.c_void_p()
        self._caps: Optional[HIDP_CAPS] = None
        self._rh: Optional[int] = None
        self._rev: Optional[int] = None

    def close(self) -> None:
        if getattr(self, "handle", None):
            if self._rh:
                kernel32.CancelIoEx(self._rh, None)
                kernel32.CloseHandle(self._rh)
                self._rh = None
            if self._rev:
                kernel32.CloseHandle(self._rev)
                self._rev = None
            if self._pp:
                hid.HidD_FreePreparsedData(self._pp)
                self._pp = ctypes.c_void_p()
            kernel32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def caps(self) -> HIDP_CAPS:
        if self._caps is None:
            caps = HIDP_CAPS()
            if not self._pp or hid.HidP_GetCaps(self._pp, ctypes.byref(caps)) != HIDP_STATUS_SUCCESS:
                raise HidError("HidP_GetCaps 失败")
            self._caps = caps
        return self._caps

    def value_caps(self, report_type: int = HidP_Feature) -> list[HIDP_VALUE_CAPS]:
        if not self._pp:
            return []
        n = wintypes.USHORT(0)
        hid.HidP_GetValueCaps(report_type, None, ctypes.byref(n), self._pp)
        if not n.value:
            return []
        arr = (HIDP_VALUE_CAPS * n.value)()
        if hid.HidP_GetValueCaps(report_type, ctypes.byref(arr), ctypes.byref(n),
                                 self._pp) != HIDP_STATUS_SUCCESS:
            return []
        return [arr[i] for i in range(n.value)]

    def get_feature_report(self, report_id: int, length: Optional[int] = None) -> bytes:
        if length is None:
            length = self.caps.FeatureReportByteLength or 64
        buf = ctypes.create_string_buffer(length)
        buf[0] = bytes([report_id & 0xFF])
        if not hid.HidD_GetFeature(self.handle, buf, length):
            raise HidError(f"读 Feature 0x{report_id:02X} 失败")
        return buf.raw

    def set_feature_report(self, data: bytes) -> None:
        if not self.write_access:
            raise HidError("无写权限")
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        if not hid.HidD_SetFeature(self.handle, buf, len(data)):
            raise HidError(f"写 Feature 失败(err={ctypes.get_last_error()})")

    def write_output(self, data: bytes) -> None:
        if not self.write_access:
            raise HidError("无写权限")
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        if hid.HidD_SetOutputReport(self.handle, buf, len(data)):
            return
        written = wintypes.DWORD(0)
        if not kernel32.WriteFile(self.handle, buf, len(data),
                                  ctypes.byref(written), None):
            raise HidError(f"WriteFile 失败(err={ctypes.get_last_error()})")

    def read_input(self, timeout_ms: int = 200, length: Optional[int] = None,
                   stop_flag: Optional[Callable[[], bool]] = None) -> Optional[bytes]:
        if not self.caps.InputReportByteLength or not self._open_reader():
            return None
        if length is None:
            length = self.caps.InputReportByteLength
        buf = ctypes.create_string_buffer(length)
        ov = OVERLAPPED()
        ov.hEvent = self._rev
        read = wintypes.DWORD(0)
        kernel32.ResetEvent(self._rev)
        ok = kernel32.ReadFile(self._rh, buf, length, ctypes.byref(read), ctypes.byref(ov))
        if ok:
            return buf.raw[:read.value]
        if ctypes.get_last_error() != ERROR_IO_PENDING:
            return None
        deadline = time.monotonic() + max(timeout_ms, 1) / 1000.0
        while True:
            if stop_flag is not None and stop_flag():
                self._cancel(ov)
                return None
            remain = deadline - time.monotonic()
            if remain <= 0:
                self._cancel(ov)
                return None
            r = kernel32.WaitForSingleObject(self._rev, int(min(remain, 0.2) * 1000) or 1)
            if r == WAIT_OBJECT_0:
                done = wintypes.DWORD(0)
                if kernel32.GetOverlappedResult(self._rh, ctypes.byref(ov),
                                                ctypes.byref(done), False):
                    return buf.raw[:done.value]
                return None

    def _cancel(self, ov) -> None:
        kernel32.CancelIoEx(self._rh, ctypes.byref(ov))
        kernel32.WaitForSingleObject(self._rev, 200)
        done = wintypes.DWORD(0)
        kernel32.GetOverlappedResult(self._rh, ctypes.byref(ov), ctypes.byref(done), False)

    def _open_reader(self) -> bool:
        if self._rh:
            return True
        h = kernel32.CreateFileW(self.path, GENERIC_READ,
                                 FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                                 OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
        if not h or h == INVALID_HANDLE_VALUE:
            return False
        ev = kernel32.CreateEventW(None, True, False, None)
        if not ev:
            kernel32.CloseHandle(h)
            return False
        self._rh, self._rev = h, ev
        return True

    def drain(self) -> None:
        if not self.caps.InputReportByteLength:
            return
        for _ in range(64):
            if self.read_input(timeout_ms=8) is None:
                return

    def usage_value(self, report_type: int, report: bytes,
                    caps: HIDP_VALUE_CAPS) -> Optional[int]:
        if not self._pp:
            return None
        value = wintypes.ULONG(0)
        status = hid.HidP_GetUsageValue(
            report_type, caps.UsagePage, caps.LinkCollection,
            caps.u.NotRange.Usage, ctypes.byref(value), self._pp,
            ctypes.create_string_buffer(bytes(report), len(report)), len(report))
        return int(value.value) if status == HIDP_STATUS_SUCCESS else None
