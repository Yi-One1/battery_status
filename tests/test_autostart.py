"""开机自启：注册表值名迁移（PeripheralBattery → PowerHub）。

用内存版 winreg 替身跑，绝不碰真实注册表。
"""
from __future__ import annotations

import sys
import types

import pytest

from app.core import autostart


class FakeKey:
    def __init__(self, values: dict) -> None:
        self.values = values

    def __enter__(self) -> "FakeKey":
        return self

    def __exit__(self, *exc) -> bool:
        return False


@pytest.fixture()
def run_key(monkeypatch):
    store: dict[str, str] = {}
    mod = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        KEY_READ=1,
        KEY_SET_VALUE=2,
        REG_SZ=1,
        OpenKey=lambda root, path, reserved=0, access=0: FakeKey(store),
        CreateKeyEx=lambda root, path, reserved=0, access=0: FakeKey(store),
    )

    def query(key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name], 1

    def set_value(key, name, reserved, kind, value):
        key.values[name] = value

    def delete_value(key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        del key.values[name]

    mod.QueryValueEx = query
    mod.SetValueEx = set_value
    mod.DeleteValue = delete_value
    monkeypatch.setitem(sys.modules, "winreg", mod)
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    return store


def test_value_name_is_powerhub():
    assert autostart.VALUE_NAME == "PowerHub"


def test_legacy_value_counts_as_enabled(run_key):
    """老用户注册表里只有旧值 PeripheralBattery，也要认成「已开启」。"""
    run_key["PeripheralBattery"] = '"D:/桌面/APP/设备电量显示.exe" --tray'
    assert autostart.is_enabled() is True


def test_enable_migrates_legacy_value(run_key):
    """开启自启：写入新值，同时删掉旧值，避免新旧两条同时启动。"""
    run_key["PeripheralBattery"] = '"old.exe" --tray'

    assert autostart.apply(True) is True
    assert autostart.VALUE_NAME in run_key
    assert "PeripheralBattery" not in run_key
    assert run_key[autostart.VALUE_NAME].endswith("--tray")


def test_disable_removes_both_values(run_key):
    run_key["PeripheralBattery"] = '"old.exe" --tray'
    run_key[autostart.VALUE_NAME] = '"new.exe" --tray'

    assert autostart.apply(False) is True
    assert run_key == {}


def test_is_enabled_false_when_empty(run_key):
    assert autostart.is_enabled() is False
