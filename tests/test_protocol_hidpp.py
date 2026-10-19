"""HID++ 有线模式探测测试。

有线（插着充电线）时鼠标不在接收器的 1~6 号索引上，
而是直接以 0xFF 出现在 HID++ 通道上。这条路径以前被
`asleep` 判断挡掉了，导致"插线充电时界面显示休眠"。
"""
from __future__ import annotations

import pytest

from app.core.protocols import (
    FEATURE_BATTERY_STATUS,
    FEATURE_UNIFIED_BATTERY,
    HidppDevice,
    HidppError,
    HidppTimeout,
    _probe_wired,
    hidpp_probe,
)


class FakeSession:
    """只实现探测用到的三个方法，避免真的去开 HID 句柄。"""

    def __init__(
        self,
        *,
        features: dict[int, int] | None = None,
        present: tuple[int, ...] = (),
        name: str = "PRO X Wireless",
        timeout: bool = False,
        error: bool = False,
    ) -> None:
        self.features = features or {}
        self.present = set(present)
        self.name = name
        self.timeout = timeout
        self.error = error
        self.calls: list[tuple[int, int]] = []

    def state_of(self, index: int) -> str:
        return "present" if index in self.present else "asleep"

    def get_feature(self, index: int, feature_id: int) -> int:
        self.calls.append((index, feature_id))
        if self.timeout:
            raise HidppTimeout("无应答")
        if self.error:
            raise HidppError(0x0A)
        return self.features.get(feature_id, 0)

    def device_name(self, index: int) -> str:
        return self.name


def test_probe_wired_finds_unified_battery():
    session = FakeSession(features={FEATURE_UNIFIED_BATTERY: 6})
    device = _probe_wired(session)
    assert device == HidppDevice(index=0xFF, name="PRO X Wireless", wired=True)


def test_probe_wired_falls_back_to_legacy_battery():
    session = FakeSession(features={FEATURE_BATTERY_STATUS: 3})
    assert _probe_wired(session) is not None


def test_probe_wired_returns_none_without_battery_feature():
    session = FakeSession(features={})
    assert _probe_wired(session) is None


def test_probe_wired_swallows_timeout():
    """超时必须吞掉：get_feature 只捕获 HidppError，漏出去会变成"读取异常"。"""
    assert _probe_wired(FakeSession(timeout=True)) is None


def test_probe_wired_swallows_device_error():
    assert _probe_wired(FakeSession(error=True)) is None


def test_hidpp_probe_uses_wired_index_when_nothing_present():
    session = FakeSession(features={FEATURE_UNIFIED_BATTERY: 6})
    probe = hidpp_probe(session, b"fake-wired-path", force=True)
    assert [d.index for d in probe.devices] == [0xFF]
    assert probe.devices[0].wired is True
    assert probe.asleep is False


def test_hidpp_probe_prefers_receiver_indexes():
    session = FakeSession(features={FEATURE_UNIFIED_BATTERY: 6}, present=(1,))
    probe = hidpp_probe(session, b"fake-receiver-path", force=True)
    assert [d.index for d in probe.devices] == [1]
    # 已经找到设备就不该再去问 0xFF
    assert (0xFF, FEATURE_UNIFIED_BATTERY) not in session.calls


def test_hidpp_probe_stays_asleep_when_truly_silent():
    """接收器在、鼠标睡着：0xFF 也没有电量特性，仍然报告休眠。"""
    session = FakeSession(features={}, timeout=True)
    probe = hidpp_probe(session, b"fake-sleeping-path", force=True)
    assert probe.devices == []
    assert probe.asleep is True


@pytest.mark.parametrize("feature", [FEATURE_UNIFIED_BATTERY, FEATURE_BATTERY_STATUS])
def test_wired_probe_tries_both_feature_ids(feature):
    session = FakeSession(features={feature: 4})
    _probe_wired(session)
    assert session.calls[0] == (0xFF, FEATURE_UNIFIED_BATTERY)
