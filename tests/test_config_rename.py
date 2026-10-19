"""设备重命名测试（问题3）。"""
from __future__ import annotations

import json

import pytest

from app.core.config import DeviceConfig, DeviceRegistry


@pytest.fixture()
def registry(tmp_path):
    return DeviceRegistry(user_dir=tmp_path)


def test_rename_builtin_device_writes_user_override(registry, tmp_path):
    """重命名内置设备会把用户覆盖写入 user 层，重启后仍生效。"""
    cfg = registry.enabled_devices()[0]
    old_name = cfg.display_name
    assert registry.rename_device(cfg.id, "我的 " + old_name) is True

    # 运行时视图已更新
    assert registry.get(cfg.id).display_name == "我的 " + old_name

    # 用户配置里有覆盖记录
    payload = json.loads((tmp_path / "devices.json").read_text(encoding="utf-8"))
    user_dev = next(d for d in payload["devices"] if d["id"] == cfg.id)
    assert user_dev["display_name"] == "我的 " + old_name

    # 重启后仍然生效
    again = DeviceRegistry(user_dir=tmp_path)
    assert again.get(cfg.id).display_name == "我的 " + old_name


def test_rename_to_same_name_is_noop(registry):
    cfg = registry.enabled_devices()[0]
    assert registry.rename_device(cfg.id, cfg.display_name) is False


def test_rename_empty_name_is_rejected(registry):
    cfg = registry.enabled_devices()[0]
    assert registry.rename_device(cfg.id, "  ") is False
