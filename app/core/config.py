"""配置层：设备清单（只读模板 + 用户覆盖）与偏好设置（可写）。"""
from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import bundled_config_dir, user_data_dir

log = logging.getLogger(__name__)

DEFAULT_TRAY_MODE = "per-device"
STYLES = ("ring", "bar", "number")


# --------------------------------------------------------------------------- #
# 设备配置
# --------------------------------------------------------------------------- #
@dataclass
class DeviceConfig:
    id: str
    type: str
    display_name: str
    subtitle: str = ""
    enabled: bool = True
    match: dict[str, Any] = field(default_factory=dict)
    protocol: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    @property
    def protocol_name(self) -> str:
        return str(self.protocol.get("name", "hid")).lower()

    @property
    def demo_level(self) -> int:
        return int(self.options.get("demo_level", 70))


@dataclass
class TypeConfig:
    key: str
    label: str
    color: str
    style: str


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _int_list(value: Any) -> list[int]:
    out: list[int] = []
    for item in _as_list(value):
        try:
            out.append(int(str(item), 0))
        except (TypeError, ValueError):
            continue
    return out


class DeviceRegistry:
    """设备清单。内置 app/config/devices.json，用户可在
    %APPDATA%/PeripheralBattery/devices.json 追加或覆盖同名条目。"""

    def __init__(self) -> None:
        self.types: dict[str, TypeConfig] = {}
        self.devices: list[DeviceConfig] = []
        self.version = "0"
        self.reload()

    # -- 加载 ---------------------------------------------------------------
    def reload(self) -> None:
        data: dict[str, Any] = {}
        bundled = bundled_config_dir() / "devices.json"
        if bundled.exists():
            data = self._read_json(bundled)
        else:
            log.error("内置设备配置缺失：%s", bundled)
        self.version = str(data.get("version", "0"))
        self.types = self._parse_types(data.get("types") or {})
        self.devices = self._parse_devices(data.get("devices") or [])

        override = user_data_dir() / "devices.json"
        if override.exists():
            extra = self._read_json(override)
            log.info("加载用户设备覆盖：%s", override)
            for key, value in (extra.get("types") or {}).items():
                base = self.types.get(key)
                merged = {
                    "label": (base.label if base else key),
                    "color": (base.color if base else "#0A84FF"),
                    "style": (base.style if base else "ring"),
                }
                merged.update(value or {})
                self.types[key] = TypeConfig(key, str(merged["label"]), str(merged["color"]), str(merged["style"]))
            by_id = {d.id: d for d in self.devices}
            for raw in extra.get("devices") or []:
                cfg = self._parse_device(raw)
                if cfg is None:
                    continue
                by_id[cfg.id] = cfg
            self.devices = list(by_id.values())

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.error("读取配置失败 %s: %s", path, exc)
            return {}

    def _parse_types(self, raw: dict[str, Any]) -> dict[str, TypeConfig]:
        types: dict[str, TypeConfig] = {}
        for key, value in raw.items():
            value = value or {}
            style = str(value.get("style", "ring"))
            types[key] = TypeConfig(
                key=key,
                label=str(value.get("label", key)),
                color=str(value.get("color", "#0A84FF")),
                style=style if style in STYLES else "ring",
            )
        return types

    def _parse_devices(self, raw: list[Any]) -> list[DeviceConfig]:
        out: list[DeviceConfig] = []
        for item in raw:
            cfg = self._parse_device(item)
            if cfg:
                out.append(cfg)
        return out

    def _parse_device(self, item: Any) -> DeviceConfig | None:
        if not isinstance(item, dict) or not item.get("id"):
            return None
        return DeviceConfig(
            id=str(item["id"]),
            type=str(item.get("type") or "mouse"),
            display_name=str(item.get("display_name") or item["id"]),
            subtitle=str(item.get("subtitle") or ""),
            enabled=bool(item.get("enabled", True)),
            match=dict(item.get("match") or {}),
            protocol=dict(item.get("protocol") or {}),
            options=dict(item.get("options") or {}),
            notes=str(item.get("notes") or ""),
        )

    # -- 匹配 ---------------------------------------------------------------
    def enabled_devices(self) -> list[DeviceConfig]:
        return [d for d in self.devices if d.enabled]

    def get(self, device_id: str) -> DeviceConfig | None:
        for device in self.devices:
            if device.id == device_id:
                return device
        return None

    def type_of(self, device: DeviceConfig) -> TypeConfig:
        return self.types.get(device.type) or TypeConfig(device.type, device.type, "#0A84FF", "ring")

    @staticmethod
    def matches(info: Any, match: dict[str, Any]) -> bool:
        """按 match 规则判断一条 HID 设备信息是否命中。"""
        if not match:
            return False
        vids = _int_list(match.get("vid"))
        pids = _int_list(match.get("pid"))
        pages = _int_list(match.get("usage_page"))
        names = [str(x).lower() for x in _as_list(match.get("product_contains"))]

        if vids and int(getattr(info, "vid", -1)) not in vids:
            return False
        if pids and int(getattr(info, "pid", -1)) not in pids:
            return False
        if pages and int(getattr(info, "usage_page", -1)) not in pages:
            return False
        if names:
            haystack = f"{getattr(info, 'product', '')} {getattr(info, 'manufacturer', '')}".lower()
            if not any(name in haystack for name in names if name):
                return False
        return True


# --------------------------------------------------------------------------- #
# 偏好设置
# --------------------------------------------------------------------------- #
@dataclass
class Preferences:
    colors: dict[str, str] = field(default_factory=dict)
    styles: dict[str, str] = field(default_factory=dict)
    refresh_seconds: int = 60
    tray_mode: str = DEFAULT_TRAY_MODE
    tray_show_percent: bool = False
    autostart: bool = False
    start_minimized: bool = True
    low_battery_threshold: int = 20
    notifications: bool = True
    glass_blur: bool = False
    demo: bool = False

    # -- 序列化 -------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.__dict__)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], registry: DeviceRegistry) -> "Preferences":
        prefs = cls()
        for key, value in (raw or {}).items():
            if not hasattr(prefs, key):
                continue
            default = getattr(prefs, key)
            if isinstance(default, dict) and isinstance(value, dict):
                setattr(prefs, key, dict(value))
            elif isinstance(default, bool):
                setattr(prefs, key, bool(value))
            elif isinstance(default, int):
                try:
                    setattr(prefs, key, int(value))
                except (TypeError, ValueError):
                    pass
            elif isinstance(default, str):
                setattr(prefs, key, str(value))
        prefs.refresh_seconds = max(10, min(600, prefs.refresh_seconds))
        prefs.low_battery_threshold = max(0, min(90, prefs.low_battery_threshold))
        if prefs.tray_mode not in ("per-device", "merged"):
            prefs.tray_mode = DEFAULT_TRAY_MODE
        prefs.sanitize_types(registry)
        return prefs

    def sanitize_types(self, registry: DeviceRegistry) -> None:
        for key, type_cfg in registry.types.items():
            color = self.colors.get(key) or type_cfg.color
            self.colors[key] = color if isinstance(color, str) and color.startswith("#") else type_cfg.color
            style = self.styles.get(key) or type_cfg.style
            self.styles[key] = style if style in STYLES else type_cfg.style

    # -- 访问 ---------------------------------------------------------------
    def color_for(self, type_key: str) -> str:
        return self.colors.get(type_key, "#0A84FF")

    def style_for(self, type_key: str) -> str:
        return self.styles.get(type_key, "ring")


class SettingsStore:
    """偏好设置读写（JSON）。"""

    def __init__(self, registry: DeviceRegistry) -> None:
        self.registry = registry
        self.path = user_data_dir() / "preferences.json"
        self.prefs = self.load()

    def load(self) -> Preferences:
        raw: dict[str, Any] = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                log.error("读取偏好设置失败：%s", exc)
        return Preferences.from_dict(raw, self.registry)

    def save(self) -> None:
        try:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.prefs.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as exc:  # noqa: BLE001
            log.error("保存偏好设置失败：%s", exc)
