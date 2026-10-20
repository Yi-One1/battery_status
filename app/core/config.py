"""配置层：设备清单（内置模板 + 用户层 + 墓碑）与偏好设置（可写）。

设备清单分三层，职责清晰：

1. **内置模板** ``app/config/devices.json``
   随程序升级一起更新，是"出厂默认"。用户不该直接改它。
2. **用户层** ``%APPDATA%/BatteryGlass/devices.json``
   程序写入。里面有三样东西：
   * ``devices`` —— 用户新增的设备（``source`` = user / auto）
   * ``removed`` —— 墓碑列表。删掉一台内置设备后，它的 id 记在这里，
     否则下次启动重新读内置模板又会把它变回来。
   * ``types``   —— 设备类型的外观覆盖（颜色/样式）
3. **运行时视图** ``self.devices`` —— 前两层合并、剔除墓碑后的最终清单。
"""
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

SOURCE_BUILTIN = "builtin"
SOURCE_USER = "user"
SOURCE_AUTO = "auto"

USER_SCHEMA_VERSION = "1"

DEFAULT_OFFLINE_HIDE_MINUTES = 10

# 背景模式
BACKGROUND_LIVE_GLASS = "live_glass"
BACKGROUND_WHITE = "white"
BACKGROUND_MODES = (BACKGROUND_LIVE_GLASS, BACKGROUND_WHITE)


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
    source: str = SOURCE_BUILTIN

    @property
    def protocol_name(self) -> str:
        return str(self.protocol.get("name", "hid")).lower()

    @property
    def demo_level(self) -> int:
        return int(self.options.get("demo_level", 70))

    @property
    def is_user_device(self) -> bool:
        return self.source in (SOURCE_USER, SOURCE_AUTO)

    def to_raw(self) -> dict[str, Any]:
        """序列化回 devices.json 的条目形态（省略空字段，便于人读）。"""
        raw: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "display_name": self.display_name,
            "enabled": bool(self.enabled),
            "match": copy.deepcopy(self.match),
            "protocol": copy.deepcopy(self.protocol),
            "options": copy.deepcopy(self.options),
            "source": self.source,
        }
        if self.subtitle:
            raw["subtitle"] = self.subtitle
        if self.notes:
            raw["notes"] = self.notes
        return raw


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
    """设备清单：内置模板 + 用户层，支持新增 / 删除 / 恢复。"""

    def __init__(self, config_dir: Path | None = None, user_dir: Path | None = None) -> None:
        self.config_dir = Path(config_dir) if config_dir is not None else bundled_config_dir()
        self.user_dir = Path(user_dir) if user_dir is not None else user_data_dir()
        self.types: dict[str, TypeConfig] = {}
        self.devices: list[DeviceConfig] = []
        self.version = "0"
        self._bundled_devices: list[DeviceConfig] = []
        self._bundled_types: dict[str, TypeConfig] = {}
        self._user_raw: dict[str, dict[str, Any]] = {}
        self._user_types: dict[str, Any] = {}
        self._removed: list[str] = []
        self.reload()

    @property
    def user_config_path(self) -> Path:
        return self.user_dir / "devices.json"

    # -- 加载 ---------------------------------------------------------------
    def reload(self) -> None:
        data: dict[str, Any] = {}
        bundled = self.config_dir / "devices.json"
        if bundled.exists():
            data = self._read_json(bundled)
        else:
            log.error("内置设备配置缺失：%s", bundled)
        self.version = str(data.get("version", "0"))
        self._bundled_types = self._parse_types(data.get("types") or {})
        self._bundled_devices = self._parse_devices(data.get("devices") or [])

        self._user_raw = {}
        self._user_types = {}
        self._removed = []
        path = self.user_config_path
        if path.exists():
            extra = self._read_json(path)
            log.info("加载用户设备配置：%s", path)
            self._user_types = dict(extra.get("types") or {})
            self._removed = [str(item) for item in (extra.get("removed") or []) if str(item)]
            for raw in extra.get("devices") or []:
                if not isinstance(raw, dict) or not raw.get("id"):
                    continue
                device_id = str(raw["id"])
                self._user_raw[device_id] = {**raw, "source": str(raw.get("source") or SOURCE_USER)}
        self._rebuild()

    def _rebuild(self) -> None:
        """把「内置 + 用户 - 墓碑」合成为运行时视图，不碰磁盘。"""
        types = dict(self._bundled_types)
        for key, value in self._user_types.items():
            base = types.get(key)
            merged = {
                "label": (base.label if base else key),
                "color": (base.color if base else "#0A84FF"),
                "style": (base.style if base else "ring"),
            }
            merged.update(value or {})
            style = str(merged["style"])
            if style not in STYLES:
                style = base.style if base else "ring"
            types[key] = TypeConfig(key, str(merged["label"]), str(merged["color"]), style)
        self.types = types

        removed = set(self._removed)
        merged_devices: dict[str, DeviceConfig] = {}
        for cfg in self._bundled_devices:
            if cfg.id in removed:
                continue
            merged_devices[cfg.id] = cfg
        for device_id, raw in self._user_raw.items():
            if device_id in removed:
                continue
            # 对来源为 builtin 的用户副本，与最新内置配置做深度合并：
            # 用户改过的字段保留，内置新增的字段（如 wired_pid）自动继承。
            bundled = next((d for d in self._bundled_devices if d.id == device_id), None)
            if bundled and str(raw.get("source") or SOURCE_USER) == SOURCE_BUILTIN:
                raw = self._merge_device_raw(bundled.to_raw(), raw)
            cfg = self._parse_device(raw, default_source=SOURCE_USER)
            if cfg is None:
                continue
            merged_devices[device_id] = cfg
        self.devices = list(merged_devices.values())

    @staticmethod
    def _merge_device_raw(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """深度合并设备配置：override 非空字段优先，base 提供默认值。

        用于来源为 builtin 的用户副本随程序升级自动继承新字段。
        """
        merged: dict[str, Any] = copy.deepcopy(base)
        for key, value in override.items():
            if value is None:
                continue
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = DeviceRegistry._merge_device_raw(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged

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

    def _parse_device(self, item: Any, default_source: str = SOURCE_BUILTIN) -> DeviceConfig | None:
        if not isinstance(item, dict) or not item.get("id"):
            return None
        source = str(item.get("source") or default_source)
        if source not in (SOURCE_BUILTIN, SOURCE_USER, SOURCE_AUTO):
            source = default_source
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
            source=source,
        )

    # -- 查询 ---------------------------------------------------------------
    def enabled_devices(self) -> list[DeviceConfig]:
        return [d for d in self.devices if d.enabled]

    def get(self, device_id: str) -> DeviceConfig | None:
        for device in self.devices:
            if device.id == device_id:
                return device
        return None

    def type_of(self, device: DeviceConfig) -> TypeConfig:
        return self.types.get(device.type) or TypeConfig(device.type, device.type, "#0A84FF", "ring")

    def type_options(self) -> list[tuple[str, str]]:
        return [(key, cfg.label) for key, cfg in self.types.items()]

    def is_builtin(self, device_id: str) -> bool:
        return any(d.id == device_id for d in self._bundled_devices)

    def is_removed(self, device_id: str) -> bool:
        return device_id in self._removed

    def removed_ids(self) -> list[str]:
        return list(self._removed)

    def exists(self, device_id: str) -> bool:
        return self.get(device_id) is not None or self.is_builtin(device_id)

    def known_devices(self) -> list[DeviceConfig]:
        """含"墓碑"条目的全量清单。

        用途是判断"这台硬件我们到底认不认识"：用户把迈从删掉之后，
        它的接收器仍然是同一台硬件，自动发现不该把它当成新设备再加一遍。
        """
        out: list[DeviceConfig] = list(self._bundled_devices)
        for raw in self._user_raw.values():
            cfg = self._parse_device(raw, default_source=SOURCE_USER)
            if cfg is not None:
                out.append(cfg)
        return out

    # -- 写入 ---------------------------------------------------------------
    def add_device(self, cfg: DeviceConfig, *, persist: bool = True) -> DeviceConfig:
        """新增或覆盖一条设备；同 id 的内置设备会被用户版本覆盖而不是叠加。"""
        # 新 id 却顶着 builtin 标签的话，说明是代码里直接构造的，补成 user
        if cfg.source not in (SOURCE_USER, SOURCE_AUTO) and not self.is_builtin(cfg.id):
            cfg.source = SOURCE_USER
        raw = cfg.to_raw()
        self._user_raw[cfg.id] = raw
        self._removed = [item for item in self._removed if item != cfg.id]
        self._rebuild()
        if persist:
            self.save_user_config()
        log.info("新增设备 %s（%s）", cfg.id, cfg.display_name)
        return self.get(cfg.id) or cfg

    def remove_device(self, device_id: str, *, persist: bool = True) -> bool:
        """删除设备。内置设备写墓碑，用户设备连定义一起留在文件里以便恢复。"""
        if not self.exists(device_id):
            return False
        if device_id not in self._removed:
            self._removed.append(device_id)
        self._rebuild()
        if persist:
            self.save_user_config()
        log.info("删除设备 %s", device_id)
        return True

    def restore_device(self, device_id: str, *, persist: bool = True) -> bool:
        """撤销删除（恢复内置默认，或把之前删掉的用户设备找回来）。"""
        if device_id not in self._removed:
            return False
        self._removed.remove(device_id)
        self._rebuild()
        if persist:
            self.save_user_config()
        return True

    def permanent_remove(self, device_id: str, *, persist: bool = True) -> bool:
        """彻底移除：从用户配置里删除该设备定义，并确保它不会重新出现。

        对内置设备：保留在 removed 墓碑中（否则下次启动内置模板会把它加回来）。
        对用户/自动设备：删除 _user_raw 中的定义，并移出 removed 墓碑。
        """
        if device_id not in self._removed and not self.exists(device_id):
            return False
        is_builtin = self.is_builtin(device_id)
        self._user_raw.pop(device_id, None)
        if is_builtin:
            if device_id not in self._removed:
                self._removed.append(device_id)
        elif device_id in self._removed:
            self._removed.remove(device_id)
        self._rebuild()
        if persist:
            self.save_user_config()
        log.info("彻底移除设备 %s", device_id)
        return True

    def set_enabled(self, device_id: str, enabled: bool, *, persist: bool = True) -> bool:
        cfg = self.get(device_id)
        if cfg is None:
            return False
        cfg.enabled = bool(enabled)
        self._user_raw[device_id] = cfg.to_raw()
        self._rebuild()
        if persist:
            self.save_user_config()
        return True

    def rename_device(self, device_id: str, display_name: str, *, persist: bool = True) -> bool:
        """修改设备显示名，写入用户层覆盖内置模板。"""
        cfg = self.get(device_id)
        if cfg is None:
            return False
        name = str(display_name).strip()
        if not name or name == cfg.display_name:
            return False
        raw = cfg.to_raw()
        raw["display_name"] = name
        self._user_raw[device_id] = raw
        self._rebuild()
        if persist:
            self.save_user_config()
        log.info("重命名设备 %s -> %s", device_id, name)
        return True

    def save_user_config(self) -> bool:
        payload = {
            "schema": 1,
            "version": USER_SCHEMA_VERSION,
            "devices": [copy.deepcopy(raw) for raw in self._user_raw.values()],
            "types": copy.deepcopy(self._user_types),
            "removed": list(dict.fromkeys(self._removed)),
        }
        try:
            self.user_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.user_config_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.user_config_path)
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("保存用户设备配置失败：%s", exc)
            return False

    # -- 匹配 ---------------------------------------------------------------
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
    # 按"外设类型"的颜色/样式：既是同类设备的默认值，也是新设备的初始值
    colors: dict[str, str] = field(default_factory=dict)
    styles: dict[str, str] = field(default_factory=dict)
    # 按"单台设备"的覆盖值：key 是 device id。空表示跟随所属类型
    device_colors: dict[str, str] = field(default_factory=dict)
    device_styles: dict[str, str] = field(default_factory=dict)
    refresh_seconds: int = 60
    tray_mode: str = DEFAULT_TRAY_MODE
    tray_show_percent: bool = False
    autostart: bool = False
    start_minimized: bool = True
    low_battery_threshold: int = 20
    notifications: bool = True
    demo: bool = False
    # 设备自动管理（问题3）
    auto_add: bool = True
    # 主界面设备顺序：按 id 列出优先顺序；未列出的排在后面，保持内置顺序
    device_order: list[str] = field(default_factory=list)
    # 玻璃效果：live_glass（柔光玻璃）/ white（纯白色）
    background_mode: str = BACKGROUND_LIVE_GLASS
    auto_hide_offline: bool = True
    offline_hide_minutes: int = DEFAULT_OFFLINE_HIDE_MINUTES
    # 托盘图标点击行为
    tray_click_action: str = "toggle"
    tray_double_click_action: str = "settings"

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
            elif isinstance(default, list) and isinstance(value, list):
                setattr(prefs, key, [str(item) for item in value])
            elif isinstance(default, bool):
                setattr(prefs, key, bool(value))
            elif isinstance(default, int):
                try:
                    setattr(prefs, key, int(value))
                except (TypeError, ValueError):
                    pass
            elif isinstance(default, str):
                setattr(prefs, key, str(value))
        # 旧布尔字段 live_glass 迁移到 background_mode
        if "live_glass" in (raw or {}):
            prefs.background_mode = BACKGROUND_LIVE_GLASS if bool(raw["live_glass"]) else BACKGROUND_WHITE
        if prefs.background_mode not in BACKGROUND_MODES:
            prefs.background_mode = BACKGROUND_LIVE_GLASS
        prefs.refresh_seconds = max(10, min(600, prefs.refresh_seconds))
        prefs.low_battery_threshold = max(0, min(90, prefs.low_battery_threshold))
        prefs.offline_hide_minutes = max(1, min(720, prefs.offline_hide_minutes))
        if prefs.tray_mode not in ("per-device", "merged"):
            prefs.tray_mode = DEFAULT_TRAY_MODE
        if prefs.tray_click_action not in ("toggle", "show", "settings"):
            prefs.tray_click_action = "toggle"
        if prefs.tray_double_click_action not in ("toggle", "show", "settings"):
            prefs.tray_double_click_action = "settings"
        prefs.sanitize_types(registry)
        prefs.sanitize_devices()
        return prefs

    def sanitize_types(self, registry: DeviceRegistry) -> None:
        for key, type_cfg in registry.types.items():
            color = self.colors.get(key) or type_cfg.color
            self.colors[key] = color if isinstance(color, str) and color.startswith("#") else type_cfg.color
            style = self.styles.get(key) or type_cfg.style
            self.styles[key] = style if style in STYLES else type_cfg.style

    def sanitize_devices(self) -> None:
        """丢掉非法的按设备覆盖项 —— 丢了就自动回落到类型默认，比留着一个坏值安全。"""
        for device_id, color in list(self.device_colors.items()):
            if not (isinstance(color, str) and color.startswith("#")):
                self.device_colors.pop(device_id, None)
        for device_id, style in list(self.device_styles.items()):
            if style not in STYLES:
                self.device_styles.pop(device_id, None)

    # -- 访问 ---------------------------------------------------------------
    def color_for(self, type_key: str) -> str:
        return self.colors.get(type_key, "#0A84FF")

    def style_for(self, type_key: str) -> str:
        return self.styles.get(type_key, "ring")

    @staticmethod
    def _split(target: Any) -> tuple[str, str]:
        """既接受 DeviceConfig，也接受 (device_id, type_key) 元组或纯 id。"""
        if isinstance(target, str):
            return target, ""
        if isinstance(target, tuple):
            return str(target[0]), str(target[1]) if len(target) > 1 else ""
        return str(getattr(target, "id", "")), str(getattr(target, "type", ""))

    def color_for_device(self, target: Any) -> str:
        """设备自己的颜色优先，没设过就跟随所属类型。"""
        device_id, type_key = self._split(target)
        override = self.device_colors.get(device_id)
        if isinstance(override, str) and override.startswith("#"):
            return override
        return self.color_for(type_key)

    def style_for_device(self, target: Any) -> str:
        device_id, type_key = self._split(target)
        override = self.device_styles.get(device_id)
        if isinstance(override, str) and override in STYLES:
            return override
        return self.style_for(type_key)

    def set_device_color(self, device_id: str, color: str) -> bool:
        if not (isinstance(color, str) and color.startswith("#")):
            return False
        self.device_colors[str(device_id)] = color
        return True

    def set_device_style(self, device_id: str, style: str) -> bool:
        if style not in STYLES:
            return False
        self.device_styles[str(device_id)] = style
        return True

    def has_device_override(self, device_id: str) -> bool:
        return str(device_id) in self.device_colors or str(device_id) in self.device_styles

    def reset_device(self, device_id: str) -> None:
        """撤掉单台设备的自定义，回到所属类型的默认外观。"""
        self.device_colors.pop(str(device_id), None)
        self.device_styles.pop(str(device_id), None)


class SettingsStore:
    """偏好设置读写（JSON）。"""

    def __init__(self, registry: DeviceRegistry, path: Path | None = None) -> None:
        self.registry = registry
        self.path = Path(path) if path is not None else registry.user_dir / "preferences.json"
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
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.prefs.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as exc:  # noqa: BLE001
            log.error("保存偏好设置失败：%s", exc)
