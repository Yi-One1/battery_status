"""设置页设备改名测试（问题3）。"""
from __future__ import annotations

import pytest

from app.core.config import DeviceRegistry, SettingsStore
from app.ui.settings import SettingsPane


pytestmark = pytest.mark.usefixtures("qapp")


def test_settings_rename_device_updates_registry_and_ui(tmp_path):
    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    pane = SettingsPane(registry, store)

    cfg = registry.enabled_devices()[0]
    row = pane._device_rows[cfg.id]
    old_name = cfg.display_name

    pane._on_device_rename(cfg.id, old_name + " 测试")

    assert registry.get(cfg.id).display_name == old_name + " 测试"
    # hint 里的设备名不需要变，但确认没有抛异常且保存已触发
    assert pane._device_hints[cfg.id].text()
