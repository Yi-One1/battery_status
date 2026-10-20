"""设备排序功能测试：device_order 偏好、排序纯函数、设置页上下移。"""
from __future__ import annotations

import pytest

from app.core.config import DeviceConfig, DeviceRegistry, Preferences, SettingsStore
from app.core.presence import ordered_devices


def _cfg(device_id: str) -> DeviceConfig:
    return DeviceConfig(id=device_id, type="mouse", display_name=device_id)


def test_ordered_devices_ranks_listed_ids_first():
    devices = [_cfg("a"), _cfg("b"), _cfg("c")]
    prefs = Preferences()
    prefs.device_order = ["c", "a"]
    assert [d.id for d in ordered_devices(devices, prefs)] == ["c", "a", "b"]


def test_ordered_devices_keeps_registry_order_for_unlisted():
    devices = [_cfg("a"), _cfg("b"), _cfg("c"), _cfg("d")]
    prefs = Preferences()
    prefs.device_order = ["b"]
    # 未列出的 a/c/d 保持原有相对顺序，排在 b 后面
    assert [d.id for d in ordered_devices(devices, prefs)] == ["b", "a", "c", "d"]


def test_ordered_devices_ignores_unknown_ids():
    devices = [_cfg("a"), _cfg("b")]
    prefs = Preferences()
    prefs.device_order = ["ghost", "b", "ghost2", "a"]
    assert [d.id for d in ordered_devices(devices, prefs)] == ["b", "a"]


def test_ordered_devices_empty_order_is_noop():
    devices = [_cfg("a"), _cfg("b")]
    prefs = Preferences()
    assert [d.id for d in ordered_devices(devices, prefs)] == ["a", "b"]


def test_preferences_device_order_roundtrip(tmp_path):
    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    store.prefs.device_order = ["mchose-g98pro", "logitech-gpx-superlight"]
    store.save()

    again = SettingsStore(registry, path=tmp_path / "preferences.json")
    assert again.prefs.device_order == ["mchose-g98pro", "logitech-gpx-superlight"]


def test_settings_move_device_updates_order_and_persists(qapp, tmp_path):
    """设置页点「上移/下移」后：行顺序、偏好、落盘三者一致。"""
    from app.ui.settings import SettingsPane

    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    pane = SettingsPane(registry, store)

    ids_before = [d.id for d in registry.enabled_devices()]
    assert len(ids_before) >= 2
    second = ids_before[1]

    pane._on_move_device(second, -1)

    # 1) 偏好已写入
    assert store.prefs.device_order[0] == second
    # 2) 落盘：重新加载偏好仍在
    again = SettingsStore(registry, path=tmp_path / "preferences.json")
    assert again.prefs.device_order[0] == second
    # 3) 设置页行顺序跟着变
    assert list(pane.device_widgets().keys())[0] == second


def test_settings_move_device_at_boundary_is_noop(qapp, tmp_path):
    from app.ui.settings import SettingsPane

    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    pane = SettingsPane(registry, store)

    first = [d.id for d in registry.enabled_devices()][0]
    pane._on_move_device(first, -1)
    assert store.prefs.device_order == []  # 没有可移动空间时不写顺序


def test_panel_visible_devices_follow_custom_order(qapp, tmp_path):
    """主界面可见设备顺序与 device_order 一致。"""
    from app.core.monitor import Monitor
    from app.ui.panel import Panel
    from app.ui.tray import TrayController

    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    ids = [d.id for d in registry.enabled_devices()]
    store.prefs.device_order = list(reversed(ids))
    monitor = Monitor(registry, store.prefs)
    tray = TrayController(registry, store)

    panel = Panel(registry, store, monitor, tray)
    assert [d.id for d in panel.visible_devices()] == list(reversed(ids))


def test_tray_icon_order_matches_panel_order(qapp, tmp_path, monkeypatch):
    """托盘 per-device 图标顺序与主界面（行优先）完全一致。"""
    from app.core.model import Snapshot
    from app.ui import tray as tray_module
    from app.ui.tray import TrayController

    # 离屏环境里创建/析构 QSystemTrayIcon 会段错误，用哑元替身
    from PySide6.QtCore import QObject, Signal as QtSignal

    class _FakeTrayIcon(QObject):
        activated = QtSignal(object)

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self.icons = []
            self.tooltips = []

        def setContextMenu(self, menu) -> None:
            pass

        def show(self) -> None:
            pass

        def setIcon(self, icon) -> None:
            self.icons.append(icon)

        def setToolTip(self, text) -> None:
            self.tooltips.append(text)

    monkeypatch.setattr(tray_module, "QSystemTrayIcon", _FakeTrayIcon)

    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    ids = [d.id for d in registry.enabled_devices()]
    store.prefs.device_order = list(reversed(ids))
    tray = TrayController(registry, store)

    snapshots = {
        device_id: Snapshot(device_id=device_id, level=80, charging=False, online=True)
        for device_id in ids
    }
    tray.update_snapshots(snapshots)

    assert [d.id for d in tray._visible] == list(reversed(ids))
    assert len(tray._icons) == len(ids)
