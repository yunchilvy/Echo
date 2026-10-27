# -*- coding: utf-8 -*-
"""echo/core/pushmon.py —— 被动监听主动上报电量的接收器

有一类 2.4G 接收器（英菲克 IN10 一代、部分廉价键鼠套装）根本不回答查询，
而是让设备每 2 秒自己推一包：

    03 <型号码> 40|41 <充电状态> <电量>

型号码实测集合 {0x73, 0x90, 0x93, 0x95, 0x98, 0x99}；
充电状态 1=未充电 / 2=已充满 / 3=充电中。

这包不一定落在厂商通道上 —— 英菲克就把它发在 usage 0x000A 那个 5 字节接口。
所以这里对所有"可读的小报文接口"都挂一个监听线程，收到就更新缓存，
8 秒没新包即视为离线。监听线程常年阻塞在 ReadFile 上，几乎不吃 CPU。
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from . import hidwin as hw

TYPECODES = {0x73, 0x90, 0x93, 0x95, 0x98, 0x99}
STALE_AFTER = 8.0          # 秒；上报周期 ~2s


class Reading:
    __slots__ = ("level", "charging", "full", "ts", "source", "raw")

    def __init__(self, level: int, charge_state: int, ts: float, source: str, raw: bytes):
        self.level = level
        self.charging = charge_state == 3
        self.full = charge_state == 2
        self.ts = ts
        self.source = source
        self.raw = raw

    @property
    def fresh(self) -> bool:
        return time.time() - self.ts < STALE_AFTER


def parse_frame(frame: bytes) -> Optional[tuple[int, int]]:
    """识别电量上报帧，返回 (电量, 充电状态)。"""
    if len(frame) >= 5 and frame[0] == 0x03 and frame[1] in TYPECODES \
            and frame[2] in (0x40, 0x41):
        level, charge = frame[4], frame[3]
        if 0 <= level <= 100:
            return level, charge
    return None


class PushMonitor:
    def __init__(self):
        self._readings: dict[str, Reading] = {}
        self._paths: set[str] = set()
        self._lock = threading.Lock()
        self._stop = False

    # ---------------------------------------------------------- 查询
    def get(self, group: str) -> Optional[Reading]:
        with self._lock:
            return self._readings.get(group)

    def listened(self) -> set[str]:
        with self._lock:
            return set(self._paths)

    # ---------------------------------------------------------- 挂监听
    def ensure(self, group: str, ifaces: list[hw.HidInterface]) -> None:
        for i in ifaces:
            if self._stop:
                return
            if not i.input_len or not (4 <= i.input_len <= 64):
                continue
            if i.reserved:                     # 鼠标/键盘顶层集合系统独占，读不了
                continue
            with self._lock:
                if i.path in self._paths:
                    continue
                self._paths.add(i.path)
            threading.Thread(target=self._loop, args=(group, i),
                             name="echo-push", daemon=True).start()

    def stop(self) -> None:
        self._stop = True

    def _loop(self, group: str, iface: hw.HidInterface) -> None:
        try:
            dev = hw.HidDevice(iface.path, write=False)
        except hw.HidError:
            with self._lock:
                self._paths.discard(iface.path)
            return
        try:
            while not self._stop:
                try:
                    frame = dev.read_input(timeout_ms=900)
                except hw.HidError:
                    break
                if not frame:
                    continue
                hit = parse_frame(frame)
                if hit is None:
                    continue
                level, charge = hit
                with self._lock:
                    self._readings[group] = Reading(
                        level, charge, time.time(),
                        f"设备主动上报 · {iface.usage_text}", frame)
        finally:
            dev.close()
            with self._lock:
                self._paths.discard(iface.path)


MONITOR = PushMonitor()
