# -*- coding: utf-8 -*-
"""echo/core/devices.py —— 把蓝牙与 HID 两路数据合成一份设备清单

判断"电量"之前先判断"连接"。断开的设备一律不给百分比：
蓝牙按音频端点状态核对，2.4G 按主动上报的新鲜度核对（8 秒无包即离线）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Optional

from . import audioend
from . import hidwin as hw
from . import protocols
from .btbatt import read_bluetooth
from .pushmon import MONITOR as PUSH

KIND_LABEL = {
    "mouse": "鼠标", "keyboard": "键盘", "combo": "键鼠套装",
    "earbuds": "耳机", "headset": "耳麦", "headphones": "头戴耳机",
    "speaker": "音箱", "gamepad": "手柄", "other": "设备",
}

AUDIO_KINDS = {"earbuds", "headset", "headphones", "speaker"}
SLEEP_AFTER = 900.0        # 15 分钟内没新上报：设备多半只是休眠，值仍然参考
GENERIC_NAMES = ("USB 输入设备", "HID-compliant device", "HID 设备", "符合 HID 标准的设备")


@dataclass
class Device:
    key: str
    name: str
    kind: str
    level: Optional[int] = None
    last_level: Optional[int] = None
    charging: bool = False
    connected: bool = True
    source: str = ""
    detail: str = ""
    link: str = "bluetooth"
    vid_pid: str = ""
    updated: float = 0.0
    error: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind_label"] = KIND_LABEL.get(self.kind, "设备")
        d["age"] = int(max(0, time.time() - self.updated)) if self.updated else None
        return d


# ------------------------------------------------------------------ HID 侧

def _has_vendor_channel(ifs: list[hw.HidInterface]) -> bool:
    return any(i.is_vendor_page and (i.output_len or i.feature_len) for i in ifs)


def _declares_battery(ifs: list[hw.HidInterface]) -> bool:
    for i in ifs:
        if not (i.feature_len or i.input_len) or i.reserved:
            continue
        try:
            with hw.HidDevice(i.path, write=False) as dev:
                for c in dev.value_caps(hw.HidP_Feature) + dev.value_caps(hw.HidP_Input):
                    if (c.UsagePage, c.u.NotRange.Usage) in hw.BATTERY_USAGES:
                        return True
        except Exception:
            continue
    return False


def _kind_from_usages(ifs: list[hw.HidInterface]) -> str:
    usages = {(i.usage_page, i.usage) for i in ifs}
    has_mouse = (0x0001, 0x0002) in usages
    has_kb = (0x0001, 0x0006) in usages or (0x0001, 0x0007) in usages
    if has_mouse and has_kb:
        return "combo"
    if has_mouse:
        return "mouse"
    if has_kb:
        return "keyboard"
    if (0x0001, 0x0004) in usages or (0x0001, 0x0005) in usages:
        return "gamepad"
    return "other"


def _best_name(ifs: list[hw.HidInterface]) -> tuple[str, str]:
    best, brand = "", ""
    for i in ifs:
        name = (i.product or "").strip()
        if not name or name in GENERIC_NAMES:
            continue
        if len(name) > len(best):
            best, brand = name, (i.manufacturer or "").strip()
    if not best:
        for i in ifs:
            if i.product and (i.manufacturer or "") != "Microsoft":
                best, brand = i.product.strip(), (i.manufacturer or "").strip()
                break
    if not best:
        best = f"{ifs[0].vid_pid} 无线接收器"
    return best, brand


def read_hid(user_specs: list) -> list[Device]:
    try:
        ifaces = hw.enumerate_hid()
    except Exception:
        return []

    groups: dict[str, list[hw.HidInterface]] = {}
    for i in ifaces:
        low = i.path.lower()
        if "vid_" not in low and "bthledevice" not in low:
            continue
        groups.setdefault(i.group, []).append(i)

    out: list[Device] = []
    for key, ifs in groups.items():
        if not _has_vendor_channel(ifs) and not _declares_battery(ifs):
            continue

        name, brand = _best_name(ifs)
        kind = _kind_from_usages(ifs)
        dev = Device(key=f"hid:{key}", name=name, kind=kind, link="2.4g",
                     vid_pid=ifs[0].vid_pid)
        dev.extra = {"brand": brand, "usages": [i.usage_text for i in ifs][:6]}

        # 先让被动监听挂上去，再把已有缓存当作结论
        PUSH.ensure(key, ifs)
        push = PUSH.get(key)
        if push is not None:
            dev.last_level = push.level
            age = time.time() - push.ts
            if push.fresh or age < SLEEP_AFTER:
                dev.level = push.level
                dev.charging = push.charging
                dev.connected = True
                dev.source = push.source
                dev.detail = (f"上报帧 {push.raw[:8].hex()} · "
                              f"{int(age)} 秒前")
                dev.updated = push.ts
                dev.extra["asleep"] = not push.fresh
                out.append(dev)
                continue

        listened = PUSH.listened()
        ordered = sorted(
            [i for i in ifs if i.path not in listened],
            key=lambda i: (0 if i.is_vendor_page else 1, 0 if i.feature_len else 1))

        for i in ordered:
            if i.reserved:
                continue
            try:
                r = protocols.read_battery(i, user_specs)
            except Exception as e:
                r = protocols.Reading(detail=f"异常 {e}")
            if r.ok:
                dev.level = r.level
                dev.charging = r.charging
                dev.source = r.source
                dev.detail = f"{r.detail} · {i.usage_text} · {i.vid_pid}"
                break

        dev.updated = time.time()
        if dev.level is not None:
            dev.connected = True
        else:
            dev.connected = False
            dev.source = dev.source or "未识别"
            if push is None:
                dev.error = "还没有收到上报"
            else:
                dev.error = "超过 8 秒没收到上报，设备可能已关机"
            if ordered and not dev.detail:
                dev.detail = f"{ordered[0].usage_text} · {ifs[0].vid_pid}"
        out.append(dev)
    return out


# ------------------------------------------------------------------ 蓝牙侧

def read_bt() -> list[Device]:
    out: list[Device] = []
    try:
        items = read_bluetooth()
    except Exception:
        return out
    eps = audioend.snapshot()
    for b in items:
        conn = b.connected
        if b.kind in AUDIO_KINDS:
            live = audioend.is_connected(b.name, eps)
            if live is not None:
                conn = live
        dev = Device(
            key=f"bt:{b.address}", name=b.name, kind=b.kind,
            level=b.battery if conn else None,
            last_level=b.battery,
            charging=False, connected=conn, link="bluetooth",
            source="蓝牙 HFP 电量上报",
            detail=f"{b.address} · {b.detail}",
            vid_pid=b.address, updated=time.time())
        if not conn:
            dev.error = "未连接"
            dev.detail += " · 端点未激活"
        out.append(dev)
    return out


# ------------------------------------------------------------------ 汇总

def scan(user_specs: Optional[list] = None) -> list[Device]:
    devs = read_bt() + read_hid(user_specs or [])
    order = {"earbuds": 0, "headset": 1, "headphones": 2, "speaker": 3,
             "mouse": 4, "combo": 5, "keyboard": 6, "gamepad": 7, "other": 9}
    devs.sort(key=lambda d: (0 if d.level is not None else 1,
                             order.get(d.kind, 9), d.name.lower()))
    return devs
