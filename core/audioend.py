# -*- coding: utf-8 -*-
"""echo/core/audioend.py —— 音频端点状态（判断蓝牙设备到底连没连上）

蓝牙属性的电量是**上一次连接时**写进去的缓存，断开之后仍然留着 100%。
只看那个属性会把"已经关机的耳机"显示成满电 —— 必须再确认真实连接状态。

做法：枚举 MMDevices 下的 Render / Capture 端点，读 DeviceState：
    1 = ACTIVE（正在使用，说明设备连着）
    2 = DISABLED / 4 = NOTPRESENT / 8 = UNPLUGGED（断开）
再按端点名与蓝牙设备名匹配。这样"耳机开着"和"耳机在包里"就能区分开。
"""
from __future__ import annotations

import winreg

BASE = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio"
STATE_ACTIVE = 1

# 端点的两个名字属性
PROP_IFACE_NAME = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"
PROP_DEVICE_DESC = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"
PROP_FRIENDLY = "{a45c254e-df1c-4efd-8020-67d146a850e0},14"


def _endpoints() -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for flow in ("Render", "Capture"):
        try:
            root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"{BASE}\{flow}",
                                  0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        except OSError:
            continue
        with root:
            i = 0
            while True:
                try:
                    guid = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, guid) as dev:
                        try:
                            state = winreg.QueryValueEx(dev, "DeviceState")[0]
                        except OSError:
                            state = 0
                        name = ""
                        try:
                            with winreg.OpenKey(dev, "Properties") as props:
                                for key in (PROP_IFACE_NAME, PROP_FRIENDLY, PROP_DEVICE_DESC):
                                    try:
                                        val = winreg.QueryValueEx(props, key)[0]
                                    except OSError:
                                        continue
                                    if isinstance(val, str) and val.strip():
                                        name = val.strip()
                                        break
                        except OSError:
                            pass
                        if name:
                            out.append((name, int(state)))
                except OSError:
                    continue
    return out


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def is_connected(device_name: str, endpoints: list[tuple[str, int]] | None = None) -> bool | None:
    """设备名对应的音频端点是否活跃。

    返回 True/False；找不到任何同名端点时返回 None（表示"无法用音频端点判断"，
    例如蓝牙鼠标这种没有音频端点的设备）。
    """
    if not device_name:
        return None
    keys = [_norm(device_name)]
    for suffix in ("handsfree", "handsfreeag", "a2dpsink", "stereo", "avrcp",
                   "免提", "音频"):
        keys.append(_norm(device_name) + suffix)
    eps = endpoints if endpoints is not None else _endpoints()
    matched = False
    for name, state in eps:
        n = _norm(name)
        if not any(n.startswith(k) for k in keys):
            continue
        matched = True
        if (state & 0xFF) == STATE_ACTIVE:
            return True
    return False if matched else None


def snapshot() -> list[tuple[str, int]]:
    return _endpoints()


if __name__ == "__main__":
    for n, s in _endpoints():
        print(f"{s:>12}  {n}")
