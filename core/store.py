# -*- coding: utf-8 -*-
"""echo/core/store.py —— 本地配置（%APPDATA%\\Echo\\config.json）"""
from __future__ import annotations

import json
import os
import time

APP = "Echo"

DEFAULTS = {
    "devices": {},
    "settings": {
        "autostart": False,
        "minimize_to_tray": True,
        "interval": 60,
        "theme": "dark",
        "tray_hint_shown": False,
    },
}


def app_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP)
    os.makedirs(d, exist_ok=True)
    return d


def config_path() -> str:
    return os.path.join(app_dir(), "config.json")


def protocols_path() -> str:
    return os.path.join(app_dir(), "protocols.json")


class Store:
    def __init__(self):
        self.data = json.loads(json.dumps(DEFAULTS))
        self.load()

    def load(self) -> None:
        p = config_path()
        if not os.path.exists(p):
            return
        try:
            with open(p, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return
        if isinstance(raw, dict):
            if isinstance(raw.get("devices"), dict):
                self.data["devices"] = raw["devices"]
            if isinstance(raw.get("settings"), dict):
                self.data["settings"].update(raw["settings"])

    def save(self) -> None:
        self.data["settings"]["saved_at"] = int(time.time())
        tmp = config_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, config_path())

    # -- 设备和设置 --------------------------------------------------
    @property
    def devices(self) -> dict:
        return self.data["devices"]

    @property
    def settings(self) -> dict:
        return self.data["settings"]

    def device_cfg(self, key: str) -> dict:
        return self.devices.setdefault(key, {})

    def set_device(self, key: str, field: str, value) -> None:
        cfg = self.device_cfg(key)
        if value in (None, ""):
            cfg.pop(field, None)
        else:
            cfg[field] = value
        if not cfg:
            self.devices.pop(key, None)
        self.save()

    def set_setting(self, key: str, value) -> None:
        self.settings[key] = value
        self.save()
