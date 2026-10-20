"""设备改名即时生效测试。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal as QtSignal
from PySide6.QtWidgets import QApplication


pytestmark = pytest.mark.usefixtures("qapp")


def _install_fake_tray(monkeypatch) -> list:
    """离屏环境替身：避免真建 QSystemTrayIcon（析构段错误），并把换血间隔归零。"""
    from app.ui import tray as tray_module

    monkeypatch.setattr(tray_module, "SWAP_DELAY_MS", 0)
    created: list = []

    class _FakeTrayIcon(QObject):
        activated = QtSignal(object)

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            created.append(self)

        def setContextMenu(self, menu) -> None:
            pass

        def show(self) -> None:
            pass

        def hide(self) -> None:
            pass

        def deleteLater(self) -> None:
            pass

        def setIcon(self, icon) -> None:
            pass

        def setToolTip(self, text) -> None:
            pass

    monkeypatch.setattr(tray_module, "QSystemTrayIcon", _FakeTrayIcon)
    return created


def test_rename_updates_card_and_settings_immediately(qapp, tmp_path, monkeypatch):
    """设置页改名（editingFinished）后，主界面卡片标题无需重建立即更新。"""
    from app.core.config import DeviceRegistry, SettingsStore
    from app.core.monitor import Monitor
    from app.ui.panel import Panel
    from app.ui.settings import SettingsPane
    from app.ui.tray import TrayController

    _install_fake_tray(monkeypatch)
    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    monitor = Monitor(registry, store.prefs)
    tray = TrayController(registry, store)
    panel = Panel(registry, store, monitor, tray)

    settings = SettingsPane(registry, store)
    settings.changed.connect(panel._on_settings_changed)

    device_id = registry.enabled_devices()[0].id
    old_name = registry.get(device_id).display_name
    assert panel._cards[device_id].name_label.text() == old_name

    # 模拟设置页编辑完成：_on_device_rename → registry + _commit → changed 信号
    settings._on_device_rename(device_id, "改过的名字")

    assert registry.get(device_id).display_name == "改过的名字"
    assert panel._cards[device_id].name_label.text() == "改过的名字"


def test_rename_persists_after_reload(qapp, tmp_path, monkeypatch):
    from app.core.config import DeviceRegistry, SettingsStore
    from app.ui.settings import SettingsPane

    _install_fake_tray(monkeypatch)
    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    pane = SettingsPane(registry, store)

    device_id = registry.enabled_devices()[0].id
    pane._on_device_rename(device_id, "重启后仍在")

    again = DeviceRegistry(user_dir=tmp_path)
    assert again.get(device_id).display_name == "重启后仍在"
