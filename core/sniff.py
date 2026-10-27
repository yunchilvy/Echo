# -*- coding: utf-8 -*-
"""echo/core/sniff.py —— 不认识协议时，直接抓原始帧

2.4G 接收器没有统一标准：有的要你发查询帧，有的每 2 秒自己推一包，
有的把电量藏在自己驱动的私有通道里。碰上前两种，Echo 内置的协议表能覆盖；
碰上第三种，唯一靠谱的办法是 **把设备自己的帧全抓下来看**。

这里不做任何解析猜测的承诺，只做三件事：
  1. 并行打开所有可读的 HID 输入接口，各读 N 秒；
  2. 去重、计数，把每类帧的十六进制原样列出来；
  3. 标出"数值随电量变化"的可疑字节（1..100 之间且在帧间漂移）。

输出可以直接丢给 agent（见 docs/AGENT.md）去补一套协议。
"""
from __future__ import annotations

import threading
import time

from . import hidwin as hw
from .pushmon import parse_frame

MAX_IFACES = 10
CHUNK = 0.25          # 单次读超时，决定停止的粒度


def _candidates(frames: list[bytes]) -> list[dict]:
    """在帧集合里找"像电量"的字节：稳定位置、值在 1..100、有变化或跨帧一致。"""
    if not frames:
        return []
    n = min(len(f) for f in frames)
    hits = []
    for i in range(n):
        vals = {f[i] for f in frames}
        if not all(0 < v <= 100 for v in vals):
            continue
        if len(frames) > 1 and len(vals) == 1:
            continue                    # 多帧都不变：多半是固定头/尾
        hits.append({"byte": i, "values": sorted(vals)[:6],
                     "guess": f"{max(vals)}%"})
    return hits[:4]


def _read(iface: hw.HidInterface, seconds: float, out: dict, lock) -> None:
    try:
        dev = hw.HidDevice(iface.path, write=False)
    except hw.HidError as e:
        out["error"] = str(e)
        return
    seen: dict[str, int] = {}
    try:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            frame = dev.read_input(timeout_ms=int(CHUNK * 1000))
            if not frame:
                continue
            key = frame.hex(" ")
            seen[key] = seen.get(key, 0) + 1
    except hw.HidError as e:
        out["error"] = str(e)
    finally:
        dev.close()
    with lock:
        out["frames"] = [{"hex": k, "count": v} for k, v in
                         sorted(seen.items(), key=lambda kv: -kv[1])[:12]]


def sniff(seconds: int = 8) -> dict:
    try:
        ifaces = [i for i in hw.enumerate_hid()
                  if i.input_len and not i.reserved
                  and 2 <= i.input_len <= 128]
    except Exception as e:
        return {"seconds": seconds, "error": str(e), "interfaces": []}
    ifaces = [i for i in ifaces
              if "vid_" in i.path.lower() or "bthledevice" in i.path.lower()][:MAX_IFACES]

    results = [{"name": (i.product or i.vid_pid), "vid_pid": i.vid_pid,
                "usage": i.usage_text, "in_len": i.input_len,
                "frames": []} for i in ifaces]
    lock = threading.Lock()
    threads = [threading.Thread(target=_read, args=(i, seconds, r, lock), daemon=True)
               for i, r in zip(ifaces, results)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(seconds + 3)

    for r in results:
        raw = [bytes.fromhex(f["hex"].replace(" ", "")) for f in r["frames"]]
        r["battery"] = _candidates(raw)
        for f, s in zip(r["frames"], raw):
            hit = parse_frame(s)
            if hit:
                r["push"] = {"level": hit[0], "charge": hit[1], "hex": f["hex"]}
                break
    return {"seconds": seconds, "interfaces": results}
