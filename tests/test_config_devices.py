"""设备清单读写测试（问题3 的配置层）。

覆盖三件事：
  1. 手动新增能落盘，重启后还在；
  2. 删除内置设备靠"墓碑"，不会被内置模板又变回来；
  3. 用户层与内置层的合并顺序 —— 同 id 覆盖而不是叠加。
"""
from __future__ import annotations

import json

import pytest

from app.core.config import (
    DEFAULT_OFFLINE_HIDE_MINUTES,
    SOURCE_AUTO,
    SOURCE_BUILTIN,
    SOURCE_USER,
    DeviceConfig,
    DeviceRegistry,
    Preferences,
    SettingsStore,
    TypeConfig,
)

BUILTIN_ID = "logitech-gpx-superlight"


@pytest.fixture()
def registry(tmp_path):
    return DeviceRegistry(user_dir=tmp_path)


def read_user_config(tmp_path) -> dict:
    path = tmp_path / "devices.json"
    assert path.exists(), "用户配置没有写出来"
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 内置层
# --------------------------------------------------------------------------- #
def test_bundled_devices_are_marked_builtin(registry):
    assert registry.devices, "内置清单不应为空"
    for cfg in registry.devices:
        assert cfg.source == SOURCE_BUILTIN
        assert registry.is_builtin(cfg.id)


def test_bundled_types_available_for_picker(registry):
    options = dict(registry.type_options())
    assert options.get("keyboard")
    assert options.get("gamepad")
    assert isinstance(registry.type_of(registry.devices[0]), TypeConfig)


# --------------------------------------------------------------------------- #
# 新增
# --------------------------------------------------------------------------- #
def test_add_device_persists_to_user_layer(registry, tmp_path):
    cfg = DeviceConfig(
        id="my-keyboard",
        type="keyboard",
        display_name="我的键盘",
        subtitle="TEST KB",
        match={"vid": 0x1234, "pid": 0x5678},
        protocol={"name": "hid", "parse": None},
    )
    stored = registry.add_device(cfg)

    assert stored.id == "my-keyboard"
    assert stored.source == SOURCE_USER
    assert registry.is_builtin("my-keyboard") is False
    assert registry.get("my-keyboard") is not None

    payload = read_user_config(tmp_path)
    assert [item["id"] for item in payload["devices"]] == ["my-keyboard"]
    assert payload["devices"][0]["match"] == {"vid": 0x1234, "pid": 0x5678}
    assert payload["removed"] == []

    # 重新构造等于模拟"关掉程序再打开"
    again = DeviceRegistry(user_dir=tmp_path)
    assert again.get("my-keyboard") is not None
    assert again.get("my-keyboard").display_name == "我的键盘"
    assert again.get("my-keyboard").source == SOURCE_USER


def test_add_device_without_persist_does_not_touch_disk(registry, tmp_path):
    registry.add_device(DeviceConfig(id="temp-dev", type="mouse", display_name="临时"), persist=False)
    assert registry.get("temp-dev") is not None
    assert not (tmp_path / "devices.json").exists()


def test_auto_added_device_keeps_auto_source_after_reload(registry, tmp_path):
    registry.add_device(
        DeviceConfig(id="auto-258a-01bb", type="keyboard", display_name="2.4G 接收器", source=SOURCE_AUTO)
    )
    payload = read_user_config(tmp_path)
    assert payload["devices"][0]["source"] == SOURCE_AUTO
    assert DeviceRegistry(user_dir=tmp_path).get("auto-258a-01bb").source == SOURCE_AUTO


def test_user_entry_overrides_builtin_instead_of_duplicating(registry):
    registry.add_device(
        DeviceConfig(id=BUILTIN_ID, type="mouse", display_name="改过的名字", match={"vid": 1133})
    )
    matches = [d for d in registry.devices if d.id == BUILTIN_ID]
    assert len(matches) == 1
    assert matches[0].display_name == "改过的名字"
    # 被用户改写后仍然算内置设备（删除时要写墓碑）
    assert registry.is_builtin(BUILTIN_ID)


def test_to_raw_drops_empty_optional_fields():
    raw = DeviceConfig(id="x", type="mouse", display_name="X").to_raw()
    assert "subtitle" not in raw and "notes" not in raw
    assert raw["source"] == SOURCE_BUILTIN


def test_add_device_normalizes_source_for_new_ids(registry):
    """代码里直接构造的 DeviceConfig 默认是 builtin，入册时要补成 user。"""
    stored = registry.add_device(DeviceConfig(id="brand-new", type="mouse", display_name="新设备"))
    assert stored.source == SOURCE_USER
    # 覆盖内置 id 时保留 builtin 标签，界面上仍显示"内置"
    kept = registry.add_device(DeviceConfig(id=BUILTIN_ID, type="mouse", display_name="改过"))
    assert kept.source == SOURCE_BUILTIN


# --------------------------------------------------------------------------- #
# 删除（墓碑）
# --------------------------------------------------------------------------- #
def test_remove_builtin_writes_tombstone(registry, tmp_path):
    assert registry.remove_device(BUILTIN_ID) is True
    assert registry.get(BUILTIN_ID) is None
    assert registry.is_removed(BUILTIN_ID)
    assert read_user_config(tmp_path)["removed"] == [BUILTIN_ID]

    again = DeviceRegistry(user_dir=tmp_path)
    assert again.get(BUILTIN_ID) is None, "墓碑没有生效，内置模板又把它加回来了"
    assert again.removed_ids() == [BUILTIN_ID]


def test_removed_builtin_stays_gone_after_reload(registry):
    registry.remove_device(BUILTIN_ID)
    registry.reload()
    assert registry.get(BUILTIN_ID) is None


def test_restore_device_undoes_tombstone(registry, tmp_path):
    registry.remove_device(BUILTIN_ID)
    assert registry.restore_device(BUILTIN_ID) is True
    assert registry.get(BUILTIN_ID) is not None
    assert read_user_config(tmp_path)["removed"] == []
    assert DeviceRegistry(user_dir=tmp_path).get(BUILTIN_ID) is not None


def test_remove_user_device_survives_restart(registry, tmp_path):
    registry.add_device(DeviceConfig(id="user-a", type="mouse", display_name="A"))
    registry.remove_device("user-a")
    assert registry.get("user-a") is None

    again = DeviceRegistry(user_dir=tmp_path)
    assert again.get("user-a") is None
    assert "user-a" in again.removed_ids()


def test_remove_unknown_device_returns_false(registry):
    assert registry.remove_device("no-such-device") is False
    assert registry.removed_ids() == []


def test_remove_twice_does_not_duplicate_tombstone(registry):
    registry.remove_device(BUILTIN_ID)
    registry.remove_device(BUILTIN_ID)
    assert registry.removed_ids().count(BUILTIN_ID) == 1


def test_removing_every_device_leaves_empty_list(registry):
    for cfg in list(registry.devices):
        assert registry.remove_device(cfg.id) is True
    assert registry.devices == []
    assert registry.enabled_devices() == []


def test_restore_unknown_device_returns_false(registry):
    assert registry.restore_device("never-removed") is False


# --------------------------------------------------------------------------- #
# 隔离性 / 启用开关
# --------------------------------------------------------------------------- #
def test_two_user_dirs_are_isolated(tmp_path):
    first = DeviceRegistry(user_dir=tmp_path / "a")
    second = DeviceRegistry(user_dir=tmp_path / "b")
    first.add_device(DeviceConfig(id="only-in-a", type="mouse", display_name="A"))
    assert first.get("only-in-a") is not None
    assert second.get("only-in-a") is None


def test_set_enabled_persists(registry, tmp_path):
    assert registry.set_enabled(BUILTIN_ID, False) is True
    assert registry.get(BUILTIN_ID).enabled is False
    assert BUILTIN_ID not in [d.id for d in registry.enabled_devices()]
    assert DeviceRegistry(user_dir=tmp_path).get(BUILTIN_ID).enabled is False


# --------------------------------------------------------------------------- #
# 偏好设置新增字段
# --------------------------------------------------------------------------- #
def test_preferences_auto_management_defaults():
    prefs = Preferences()
    assert prefs.auto_add is True
    assert prefs.auto_hide_offline is True
    assert prefs.offline_hide_minutes == DEFAULT_OFFLINE_HIDE_MINUTES


def test_preferences_clamps_offline_minutes(registry):
    prefs = Preferences.from_dict({"offline_hide_minutes": 99999}, registry)
    assert prefs.offline_hide_minutes == 720
    prefs = Preferences.from_dict({"offline_hide_minutes": -5}, registry)
    assert prefs.offline_hide_minutes == 1


def test_preferences_bools_roundtrip(registry, tmp_path):
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    store.prefs.auto_add = False
    store.prefs.auto_hide_offline = False
    store.prefs.offline_hide_minutes = 30
    store.save()

    again = SettingsStore(registry, path=tmp_path / "preferences.json")
    assert again.prefs.auto_add is False
    assert again.prefs.auto_hide_offline is False
    assert again.prefs.offline_hide_minutes == 30
