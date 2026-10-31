"""用户层配置冻结修复：内置协议升级必须送达老用户。

背景：用户层是「整份快照」，深合并只覆盖不删键 —— 内置删掉的键（如曾经误配的
``charging_invert``）会永远留在用户层，把后续协议修复重新盖回去。修复后 protocol
段以内置为准（只在 ``based_on`` 与内置 ``version`` 不一致时刷新）。
"""
from __future__ import annotations

import json

import pytest

from app.core.config import DeviceRegistry
from app.core.paths import bundled_config_dir

BUNDLED = json.loads((bundled_config_dir() / "devices.json").read_text(encoding="utf-8"))
TARGET_ID = "mchose-g98pro"


def bundled_device(device_id: str) -> dict:
    return next(d for d in BUNDLED["devices"] if d["id"] == device_id)


def write_user_layer(user_dir, devices: list[dict]) -> None:
    (user_dir / "devices.json").write_text(
        json.dumps(
            {"schema": 1, "version": "1", "devices": devices, "types": {}, "removed": []},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def builtin_copy(**overrides) -> dict:
    """构造一份「老用户改过名字的内置设备快照」。"""
    base = bundled_device(TARGET_ID)
    raw = {
        "id": base["id"],
        "type": base["type"],
        "display_name": "我的迈从",
        "enabled": True,
        "match": base["match"],
        "protocol": dict(base["protocol"]),
        "options": dict(base.get("options") or {}),
        "source": "builtin",
    }
    raw.update(overrides)
    return raw


def test_stale_protocol_keys_are_dropped(tmp_path):
    """内置已删除的键（charging_invert）不该在用户层永生，用户改过的名字必须保留。"""
    stale = dict(bundled_device(TARGET_ID)["protocol"])
    stale["charging_invert"] = True          # 内置早已删除的失效键
    stale["charging_bit"] = 0                # 内置改过语义的键
    write_user_layer(tmp_path, [builtin_copy(protocol=stale)])

    registry = DeviceRegistry(user_dir=tmp_path)
    cfg = registry.get(TARGET_ID)

    assert cfg is not None
    assert cfg.display_name == "我的迈从", "用户的显示名不能被刷新冲掉"
    assert "charging_invert" not in cfg.protocol, "失效键必须被清掉"
    assert cfg.protocol == bundled_device(TARGET_ID)["protocol"]


def test_refresh_is_persisted_with_based_on(tmp_path):
    """刷新后写回 based_on，避免每次启动重复刷新。"""
    write_user_layer(tmp_path, [builtin_copy()])

    registry = DeviceRegistry(user_dir=tmp_path)
    payload = json.loads((tmp_path / "devices.json").read_text(encoding="utf-8"))
    saved = next(d for d in payload["devices"] if d["id"] == TARGET_ID)

    assert saved["based_on"] == registry.version
    assert saved["display_name"] == "我的迈从"

    # 再加载一次：based_on 已对齐 → 不再触发刷新（用户协议自定义不会被二次抹掉）
    again = DeviceRegistry(user_dir=tmp_path)
    assert again.get(TARGET_ID).display_name == "我的迈从"


def test_same_version_keeps_user_protocol_tweak(tmp_path):
    """版本没变时，用户对协议的手工微调要保留（不误伤）。"""
    tweaked = dict(bundled_device(TARGET_ID)["protocol"])
    tweaked["poll_interval"] = 9
    write_user_layer(
        tmp_path,
        [builtin_copy(protocol=tweaked, based_on=BUNDLED["version"])],
    )

    registry = DeviceRegistry(user_dir=tmp_path)
    assert registry.get(TARGET_ID).protocol["poll_interval"] == 9


def test_user_defined_device_protocol_untouched(tmp_path):
    """用户自建设备（source=user）不受内置刷新影响。"""
    base = bundled_device(TARGET_ID)
    custom_protocol = dict(base["protocol"])
    custom_protocol["level_offset"] = 9
    write_user_layer(
        tmp_path,
        [
            {
                "id": "my-own-mouse",
                "type": "mouse",
                "display_name": "自制设备",
                "enabled": True,
                "match": {"vid": 0x1234},
                "protocol": custom_protocol,
                "options": {},
                "source": "user",
            }
        ],
    )

    registry = DeviceRegistry(user_dir=tmp_path)
    cfg = registry.get("my-own-mouse")
    assert cfg is not None
    assert cfg.protocol["level_offset"] == 9


def test_rename_stamps_based_on(tmp_path):
    """重命名写入用户层时带上 based_on，下次启动不会白刷一遍。"""
    registry = DeviceRegistry(user_dir=tmp_path)
    cfg = registry.enabled_devices()[0]
    assert registry.rename_device(cfg.id, "改过的名字") is True

    payload = json.loads((tmp_path / "devices.json").read_text(encoding="utf-8"))
    saved = next(d for d in payload["devices"] if d["id"] == cfg.id)
    assert saved["based_on"] == registry.version
