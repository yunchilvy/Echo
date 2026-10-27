# -*- coding: utf-8 -*-
"""echo/core/protocols.py —— 电量读取策略引擎

一个无线外设的电量可能藏在四条路里，按可靠性从高到低依次尝试：
  1. 标准 HID 电量字段（Usage Page 0x06 / 0x85 的 Battery Strength）
  2. 厂商协议表（罗技 HID++、ATK/VXE、VAXEE、Endgame、Nordic、杰理 TLV…）
  3. 用户标定过的协议（configs/protocols.json，可由"诊断"面板保存）
  4. 输入报告监听（少数设备会主动上报）

换设备时最省事的是第 3 条：Echo 会把诊断结果写成一个可复用的小条目，
不必改代码。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from . import hidwin as hw

# ---------------------------------------------------------------- 数据结构


@dataclass
class Reading:
    ok: bool = False
    level: Optional[int] = None
    charging: bool = False
    source: str = ""
    detail: str = ""
    raw: bytes = b""
    extra: dict = field(default_factory=dict)


@dataclass
class ProtocolSpec:
    name: str
    vid: int
    usage_page: int
    report_id: int
    payload: list
    length: int
    level_offset: int
    wait_ms: int = 120
    scale: int = 1
    mode: str = "auto"          # feature / output / auto
    expect: tuple = ()
    retries: int = 2
    level_max: int = 100
    charging_offset: Optional[int] = None
    notes: str = ""

    @property
    def kind(self) -> str:
        return self.mode


BUILTIN: list[ProtocolSpec] = [
    ProtocolSpec(
        name="ATK / VXE 无线鼠标",
        vid=0x373B, usage_page=0xFF05, report_id=0x08,
        payload=[0x7D, 0x72, 0x02, 0x00, 0x01, 0x07, 0x01],
        length=64, level_offset=7, wait_ms=100, mode="output",
        expect=((1, 0x72), (5, 0x07)), retries=3,
        notes="Nordic 方案；电量在回复第 7 字节"),
    ProtocolSpec(
        name="ATK / VXE / VGN 无线鼠标（旧协议）",
        vid=0x373B, usage_page=0xFF02, report_id=0x08,
        payload=[0x04] + [0x00] * 15 + [0x49],
        length=17, level_offset=6, wait_ms=100, mode="output", retries=3,
        notes="电量在回复第 6 字节"),
    ProtocolSpec(
        name="VXE / VGN 无线鼠标",
        vid=0x3554, usage_page=0xFF02, report_id=0x08,
        payload=[0x04] + [0x00] * 15 + [0x49],
        length=17, level_offset=6, wait_ms=100, mode="output", retries=3),
    ProtocolSpec(
        name="Endgame Gear 无线",
        vid=0x3367, usage_page=0xFF01, report_id=0xA1,
        payload=[0xB4], length=64, level_offset=16, wait_ms=350,
        mode="feature", expect=((1, 0x01),), retries=3,
        notes="第一次读取通常只是唤醒"),
    ProtocolSpec(
        name="VAXEE / ZYGEN 无线",
        vid=0x3057, usage_page=0xFF05, report_id=0x0E,
        payload=[0xA5, 0x0B, 0x01, 0x01], length=64, level_offset=5,
        wait_ms=120, scale=5, level_max=20, mode="feature",
        notes="以 5% 为步长上报"),
    ProtocolSpec(
        name="Nordic nRF 2.4G 接收器",
        vid=0x1915, usage_page=0xFFA0, report_id=0x05,
        payload=[0x15, 0x00, 0x00, 0x01], length=32, level_offset=9,
        wait_ms=90, mode="feature"),
]

USER_FILE = "protocols.json"


# ---------------------------------------------------------------- 工具

def _scale(value: int, logical_max: int = 100) -> int:
    if value is None:
        return 0
    if logical_max and logical_max > 100 and logical_max != 0xFF:
        return max(0, min(100, round(value * 100 / logical_max)))
    if value > 100:
        return max(0, min(100, round(value * 100 / 255)))
    return max(0, value)


def load_user_specs(path: str) -> list[ProtocolSpec]:
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    out = []
    for it in data if isinstance(data, list) else []:
        try:
            out.append(ProtocolSpec(
                name=it.get("name", "自定义协议"),
                vid=int(it["vid"], 16) if isinstance(it["vid"], str) else int(it["vid"]),
                usage_page=int(it["usage_page"], 16) if isinstance(it["usage_page"], str)
                else int(it["usage_page"]),
                report_id=int(it["report_id"], 16) if isinstance(it["report_id"], str)
                else int(it["report_id"]),
                payload=list(it.get("payload", [])),
                length=int(it.get("length", 64)),
                level_offset=int(it.get("level_offset", 0)),
                wait_ms=int(it.get("wait_ms", 150)),
                scale=int(it.get("scale", 1)),
                mode=it.get("mode", "auto"),
                expect=tuple(tuple(x) for x in it.get("expect", [])),
                level_max=int(it.get("level_max", 100)),
                notes=it.get("notes", "用户标定"),
            ))
        except Exception:
            continue
    return out


def save_user_spec(path: str, spec: ProtocolSpec) -> None:
    raw = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = []
    raw.append({
        "name": spec.name, "vid": f"{spec.vid:04X}",
        "usage_page": f"{spec.usage_page:04X}", "report_id": f"{spec.report_id:02X}",
        "payload": list(spec.payload), "length": spec.length,
        "level_offset": spec.level_offset, "wait_ms": spec.wait_ms,
        "scale": spec.scale, "mode": spec.mode,
        "level_max": spec.level_max, "notes": spec.notes,
    })
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 策略 1：标准字段

def strategy_standard(iface: hw.HidInterface) -> Optional[Reading]:
    if not iface.feature_len:
        return None
    try:
        with hw.HidDevice(iface.path, write=False) as dev:
            for cap in dev.value_caps(hw.HidP_Feature):
                key = (cap.UsagePage, cap.u.NotRange.Usage)
                if key not in hw.BATTERY_USAGES:
                    continue
                try:
                    report = dev.get_feature_report(cap.ReportID)
                except hw.HidError as e:
                    return Reading(source="标准 HID 电量字段", detail=str(e))
                value = dev.usage_value(hw.HidP_Feature, report, cap)
                if value is None:
                    continue
                return Reading(ok=True, level=_scale(value, cap.LogicalMax),
                               source=f"标准 HID 电量字段 · {hw.BATTERY_USAGES[key]}",
                               detail=f"Report 0x{cap.ReportID:02X} 原始值 {value}",
                               raw=report)
    except hw.HidError as e:
        return Reading(source="标准 HID 电量字段", detail=str(e))
    return None


# ---------------------------------------------------------------- 策略 2：厂商协议

def _matches(report: bytes, expect: tuple) -> bool:
    return bool(report) and all(len(report) > o and report[o] == v for o, v in expect)


def _send(dev: hw.HidDevice, spec: ProtocolSpec, kind: str) -> bytes:
    body = ([spec.report_id] + list(spec.payload) + [0] * spec.length)[: spec.length]
    last = b""
    for _ in range(max(1, spec.retries)):
        dev.drain()
        if kind == "feature":
            dev.set_feature_report(bytes(body))
            time.sleep(spec.wait_ms / 1000.0)
            last = dev.get_feature_report(spec.report_id, spec.length)
            if not spec.expect or _matches(last, spec.expect):
                return last
            continue
        dev.write_output(bytes(body))
        time.sleep(spec.wait_ms / 1000.0)
        end = time.monotonic() + 0.6
        while time.monotonic() < end:
            r = dev.read_input(timeout_ms=150)
            if not r:
                continue
            last = r
            if not spec.expect or _matches(r, spec.expect):
                return r
    return last


def _parse(spec: ProtocolSpec, report: bytes) -> Reading:
    if len(report) <= spec.level_offset:
        return Reading(source=spec.name, detail=f"回复长度不足 {len(report)}B", raw=report)
    raw_level = report[spec.level_offset]
    if spec.level_max and raw_level > spec.level_max:
        return Reading(source=spec.name,
                       detail=f"电量字节 0x{raw_level:02X} 超出预期", raw=report)
    charging = False
    if spec.charging_offset is not None and len(report) > spec.charging_offset:
        charging = bool(report[spec.charging_offset])
    return Reading(ok=True, level=max(0, min(100, raw_level * spec.scale)),
                   charging=charging, source=spec.name,
                   detail=f"Report 0x{spec.report_id:02X} 电量@{spec.level_offset}",
                   raw=report)


def strategy_vendor(iface: hw.HidInterface, user_specs: list[ProtocolSpec]) -> Optional[Reading]:
    specs = [s for s in BUILTIN + user_specs
             if s.vid == iface.vid and s.usage_page == iface.usage_page]
    if not specs:
        return None
    last = None
    for spec in specs:
        kind = spec.mode if spec.mode != "auto" else (
            "feature" if iface.feature_len else "output")
        try:
            with hw.HidDevice(iface.path, write=True) as dev:
                report = _send(dev, spec, kind)
            if not report:
                last = Reading(source=spec.name,
                               detail="设备没有回复（可能正在休眠，动一下设备再试）")
                continue
            res = _parse(spec, report)
            if res.ok:
                return res
            last = res
        except hw.HidError as e:
            last = Reading(source=spec.name, detail=f"收发失败：{e}")
    return last


# ---------------------------------------------------------------- 策略 3：杰理（Jieli）TLV

def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def jieli_query(dev: hw.HidDevice, seq: int, tlv_type: int, payload: bytes = b"",
                timeout: float = 0.4) -> Optional[dict]:
    """构造并发送一帧杰理私有协议，返回解析到的 TLV 字典。"""
    n = len(payload)
    frame = bytearray([0x00, 0xFE, seq & 0xFF, 0x10, 0x00, 0x02, 0x00, 0x00,
                       n & 0xFF, (n >> 8) & 0xFF]) + bytearray(payload)
    crc = crc16_ccitt(bytes(frame[1:10 + n]))
    frame += bytes([(crc >> 8) & 0xFF, crc & 0xFF])
    frame += bytes(65 - len(frame))
    dev.drain()
    try:
        dev.write_output(bytes(frame[:65]))
    except hw.HidError:
        return None
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        r = dev.read_input(timeout_ms=int(max(1, (end - time.monotonic()) * 1000)))
        if not r or len(r) < 12 or r[1] != 0xFE:
            continue
        ln = r[8] | (r[9] << 8)
        if ln + 12 > len(r):
            continue
        body = r[10:10 + ln]
        tlvs = {}
        i = 0
        while i + 1 < len(body):
            t, l = body[i], body[i + 1]
            tlvs[t] = body[i + 2:i + 2 + l]
            i += 2 + l
        return tlvs
    return None


def strategy_jieli(iface: hw.HidInterface) -> Optional[Reading]:
    if iface.vid != 0x3654 or iface.output_len != 65:
        return None
    try:
        with hw.HidDevice(iface.path, write=True) as dev:
            tlvs = jieli_query(dev, 1, 0x01)
            if not tlvs:
                return Reading(source="杰理 2.4G 耳机协议",
                               detail="dongle 没有回复（耳机可能没开机）")
            level = tlvs.get(0x01, b"")[0] if tlvs.get(0x01) else None
            if level is None:
                return Reading(source="杰理 2.4G 耳机协议", detail="回复无电量 TLV")
            return Reading(ok=True, level=level, source="杰理 2.4G 耳机协议",
                           detail="TLV 0x01 首字节为电量")
    except hw.HidError as e:
        return Reading(source="杰理 2.4G 耳机协议", detail=str(e))


# ---------------------------------------------------------------- 策略 4：罗技 HID++

_HIDPP_STATE = {0: "放电中", 1: "充电中", 2: "充电中", 3: "已充满",
                4: "充电中", 5: "电池异常", 6: "温度异常", 7: "充电异常"}


def _hidpp_call(dev, rid, index, fidx, func, params, timeout_ms=260):
    n = 20 if rid == 0x11 else 7
    body = ([rid, index, fidx, ((func & 0x0F) << 4) | 0x01] + list(params) + [0] * n)[:n]
    try:
        dev.write_output(bytes(body))
    except hw.HidError:
        return None
    end = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < end:
        r = dev.read_input(timeout_ms=max(1, int((end - time.monotonic()) * 1000)))
        if not r or len(r) < 4:
            return None
        if r[0] != rid:
            continue
        if len(r) > 3 and r[3] & 0x80:
            return None
        return r
    return None


def _hidpp_fidx(dev, rid, index, feature_id):
    r = _hidpp_call(dev, rid, index, 0x00, 0x00,
                    [feature_id >> 8, feature_id & 0xFF])
    if not r:
        return None
    for pos in (6, 4, 5):
        if len(r) > pos and r[pos]:
            return r[pos]
    return None


def strategy_logitech(iface: hw.HidInterface) -> Optional[Reading]:
    if iface.vid != 0x046D or not iface.output_len:
        return None
    try:
        with hw.HidDevice(iface.path, write=True) as dev:
            for rid in (0x11, 0x10):
                for index in (0xFF, 1, 2, 3, 4, 5, 6):
                    for feat, tag in ((0x1004, "统一电池"), (0x1000, "电池状态")):
                        fidx = _hidpp_fidx(dev, rid, index, feat)
                        if not fidx:
                            continue
                        r = _hidpp_call(dev, rid, index, fidx, 0x00, [])
                        if not r or len(r) < 6:
                            continue
                        level, st = r[4], r[5]
                        if feat == 0x1000 and len(r) >= 7:
                            st = r[6]
                        if level > 100:
                            continue
                        return Reading(
                            ok=True, level=level, charging=st in (1, 2, 4),
                            source=f"罗技 HID++ · {tag}",
                            detail=f"设备索引 {index} · {_HIDPP_STATE.get(st, f'状态 {st}')}",
                            raw=r)
    except hw.HidError as e:
        return Reading(source="罗技 HID++", detail=str(e))
    return None


# ---------------------------------------------------------------- 策略 5：监听输入报告

def strategy_listen(iface: hw.HidInterface, seconds: float = 2.5) -> Optional[Reading]:
    if not iface.input_len:
        return None
    try:
        dev = hw.HidDevice(iface.path, write=False)
    except hw.HidError as e:
        return Reading(source="输入报告监听", detail=str(e))
    result = None
    try:
        caps = [c for c in dev.value_caps(hw.HidP_Input)
                if (c.UsagePage, c.u.NotRange.Usage) in hw.BATTERY_USAGES]
        if not caps:
            return None
        end = time.monotonic() + seconds
        while time.monotonic() < end and result is None:
            r = dev.read_input(timeout_ms=300)
            if not r:
                continue
            for c in caps:
                v = dev.usage_value(hw.HidP_Input, r, c)
                if v is not None:
                    result = Reading(ok=True, level=_scale(v, c.LogicalMax),
                                     source="输入报告电量字段",
                                     detail=f"原始值 {v}", raw=r)
                    break
    except hw.HidError:
        pass
    finally:
        dev.close()
    return result


# ---------------------------------------------------------------- 调度

def read_battery(iface: hw.HidInterface, user_specs: Optional[list] = None,
                 listen: bool = False) -> Reading:
    user_specs = user_specs or []
    steps = (
        lambda: strategy_standard(iface),
        lambda: strategy_jieli(iface),
        lambda: strategy_vendor(iface, user_specs),
        lambda: strategy_logitech(iface),
    )
    last: Optional[Reading] = None
    for step in steps:
        try:
            res = step()
        except Exception as e:                       # 单策略异常不影响其他
            res = Reading(detail=f"异常 {e}")
        if res and res.ok:
            return res
        if res and not last:
            last = res
    if listen:
        try:
            res = strategy_listen(iface)
            if res and res.ok:
                return res
        except Exception:
            pass
    return last or Reading(detail="没有可用的读取方式")


# ---------------------------------------------------------------- 诊断探测

def probe_interface(iface: hw.HidInterface) -> dict:
    """给"诊断"面板用：回报接口能力 + 尝试常见查询帧 + 原始回复。

    只在用户主动点击时运行，不会在常规刷新里去碰设备。
    """
    info = {
        "vid_pid": iface.vid_pid, "usage": iface.usage_text,
        "product": iface.product, "manufacturer": iface.manufacturer,
        "in_len": iface.input_len, "out_len": iface.output_len,
        "feature_len": iface.feature_len,
        "attempts": [], "candidates": [],
    }
    frames = [
        ("0x08/0x72 电量查询", 0x08, [0x7D, 0x72, 0x02, 0x00, 0x01, 0x07, 0x01]),
        ("0x08/0x04 状态查询", 0x08, [0x04] + [0x00] * 15 + [0x49]),
        ("0x05/0x15 Nordic", 0x05, [0x15, 0x00, 0x00, 0x01]),
        ("0x0E/0xA5 VAXEE", 0x0E, [0xA5, 0x0B, 0x01, 0x01]),
        ("0x04/0x01 通用", 0x04, [0x01, 0x00, 0x00, 0x00]),
    ]
    for label, rid, payload in frames:
        if not iface.output_len:
            break
        n = iface.output_len
        body = ([rid] + payload + [0] * n)[:n]
        try:
            with hw.HidDevice(iface.path, write=True) as dev:
                dev.drain()
                dev.write_output(bytes(body))
                end = time.monotonic() + 0.5
                replies = []
                while time.monotonic() < end:
                    r = dev.read_input(timeout_ms=120)
                    if r:
                        replies.append(r.hex())
                good = [x for x in replies if x]
                info["attempts"].append({
                    "label": label, "sent": bytes(body)[:12].hex(),
                    "replies": good[:6], "count": len(good),
                })
                for rhex in good:
                    raw = bytes.fromhex(rhex)
                    for i, b in enumerate(raw[1:], 1):
                        if 1 <= b <= 100:
                            cand = f"第 {i} 字节 = {b}"
                            if cand not in info["candidates"]:
                                info["candidates"].append(cand)
        except hw.HidError as e:
            info["attempts"].append({"label": label, "sent": bytes(body)[:12].hex(),
                                     "replies": [], "error": str(e)})
    return info
