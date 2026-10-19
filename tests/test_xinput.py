"""XInput 手柄读数的测试。

这里的重点是**别撒谎**：
  * 2.4G 接收器会把自己伪装成"有线供电"，Windows 固定报满档 ——
    这时候不能说"充电中"（多数情况根本没插线），也不能把满档当成 100%；
  * 超出四档的未知档位没有可信百分比，宁可没有数字。
"""
from __future__ import annotations

import ctypes

import pytest

from app.core import xinput
from app.core.xinput import (
    BATTERY_TYPE_ALKALINE,
    BATTERY_TYPE_DISCONNECTED,
    BATTERY_TYPE_NIMH,
    BATTERY_TYPE_WIRED,
)


class _FakeLib:
    """冒充 xinput1_4.dll：只实现读代码用到的两个函数。"""

    def __init__(self, *, state_rc: int = 0, battery_rc: int = 0, battery_type: int = 0, level: int = 3):
        self.state_rc = state_rc
        self.battery_rc = battery_rc
        self.battery_type = battery_type
        self.level = level

    def XInputGetState(self, slot, ptr) -> int:  # noqa: N802
        return self.state_rc

    def XInputGetBatteryInformation(self, slot, devtype, ptr) -> int:  # noqa: N802
        if self.battery_rc:
            return self.battery_rc
        target = ptr._obj  # byref 包着的结构体
        target.BatteryType = self.battery_type
        target.BatteryLevel = self.level
        return 0


@pytest.fixture()
def fake(monkeypatch):
    def install(**kwargs) -> _FakeLib:
        lib = _FakeLib(**kwargs)
        monkeypatch.setattr(xinput, "_library", lambda: lib)
        return lib

    return install


def test_wired_dongle_is_not_reported_as_charging(fake):
    """这条是问题2的核心：2.4G 接收器报 WIRED，不能翻译成"充电中"。"""
    fake(battery_type=BATTERY_TYPE_WIRED, level=3)
    reading = xinput.read_slot(0)

    assert reading.ok is True
    assert reading.level is None
    assert reading.charging is None  # 关键：既不是 True 也不是 False，而是"不知道"
    assert "有线/接收器" in reading.detail
    assert "电压" not in reading.detail


def test_wired_level_is_never_treated_as_full(fake):
    """WIRED 时 BatteryLevel 恒为满档，绝不能折算成 100%。"""
    fake(battery_type=BATTERY_TYPE_WIRED, level=3)
    assert xinput.read_slot(0).level is None


def test_disconnected_controller_is_offline(fake):
    fake(battery_type=BATTERY_TYPE_DISCONNECTED)
    reading = xinput.read_slot(0)
    assert reading.ok is False
    assert reading.level is None


def test_pad_not_present(fake):
    fake(state_rc=1167)  # ERROR_DEVICE_NOT_CONNECTED
    reading = xinput.read_slot(2)
    assert reading.ok is False
    assert "未连接" in reading.detail


def test_battery_query_failure_is_reported(fake):
    fake(battery_rc=1)
    reading = xinput.read_slot(0)
    assert reading.ok is False
    assert "未提供电池信息" in reading.detail


@pytest.mark.parametrize(
    "battery_type, level, percent",
    [
        (BATTERY_TYPE_ALKALINE, 0, 5),
        (BATTERY_TYPE_ALKALINE, 1, 25),
        (BATTERY_TYPE_NIMH, 2, 60),
        (BATTERY_TYPE_NIMH, 3, 100),
    ],
)
def test_four_level_mapping(fake, battery_type, level, percent):
    fake(battery_type=battery_type, level=level)
    reading = xinput.read_slot(0)
    assert reading.ok is True
    assert reading.level == percent
    assert reading.charging is False


def test_unknown_level_yields_no_number(fake):
    """四档之外的值没有可信百分比，不许硬凑一个 100 出来。"""
    fake(battery_type=BATTERY_TYPE_ALKALINE, level=9)
    reading = xinput.read_slot(0)
    assert reading.ok is True
    assert reading.level is None
    assert "未知电量档位" in reading.detail


def test_status_text_never_says_charging_for_a_dongle():
    """把读数拼成快照后，界面文字也不能出现「充电中」。"""
    from app.core.model import Snapshot

    snapshot = Snapshot(
        device_id="flydigi-apex4",
        level=None,
        charging=None,
        online=True,
        source="xinput",
        detail="手柄 0 · 有线/接收器接入，XInput 不提供电量档位",
    )
    assert snapshot.status_text() == "暂无电量"


def test_missing_xinput_runtime(monkeypatch):
    monkeypatch.setattr(xinput, "_library", lambda: None)
    reading = xinput.read_slot(0)
    assert reading.ok is False
    assert "XInput" in reading.detail


def test_slot_auto_picks_first_free(monkeypatch):
    fake = _FakeLib(battery_type=BATTERY_TYPE_ALKALINE, level=2)
    monkeypatch.setattr(xinput, "_library", lambda: fake)
    monkeypatch.setattr(xinput, "connected_slots", lambda: [0, 1])

    used: set[int] = {0}
    reading = xinput.read_device("auto", used)
    assert reading.ok and reading.level == 60
    assert used == {0, 1}


def test_battery_structure_layout():
    """结构体字段顺序错了就会把 BatteryType/Level 读反。"""
    info = xinput._BatteryInformation()
    info.BatteryType = 2
    info.BatteryLevel = 3
    raw = bytes(ctypes.string_at(ctypes.byref(info), ctypes.sizeof(info)))
    assert raw[0] == 2 and raw[1] == 3
