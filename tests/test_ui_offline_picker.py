"""断连收起时长控件测试：1 秒 ~ 1 小时可精确选择。"""
from __future__ import annotations

import pytest

from app.ui.settings import OFFLINE_PRESETS, OFFLINE_UNITS, OfflineHidePicker


@pytest.fixture()
def picker(qapp):
    return OfflineHidePicker(600)


def test_default_shows_ten_minutes(picker):
    """600 秒应回填成「10 分钟」而不是 600 秒。"""
    assert picker.value() == 600
    assert picker.spin.value() == 10
    assert picker.unit.currentData() == 60


def test_accepts_one_second(qapp):
    """下限 1 秒：以前最小只有 1 分钟，现在能选到秒级。"""
    picker = OfflineHidePicker(1)
    assert picker.value() == 1
    assert picker.spin.value() == 1
    assert picker.unit.currentData() == 1


def test_spin_changes_emit_seconds(qapp):
    picker = OfflineHidePicker(600)
    seen: list[int] = []
    picker.changed.connect(seen.append)

    picker.spin.setValue(3)          # 3 × 分钟 = 180 秒
    assert picker.value() == 180
    assert seen == [180]


def test_preset_click_sets_seconds(qapp):
    picker = OfflineHidePicker(600)
    seen: list[int] = []
    picker.changed.connect(seen.append)

    picker.presets.changed.emit("1800")
    assert picker.value() == 1800
    assert picker.spin.value() == 30
    assert seen == [1800]


def test_unit_switch_keeps_number(qapp):
    """数值 + 单位是一个整体输入：10 分钟切到「秒」就是 10 秒，不是 600 秒。"""
    picker = OfflineHidePicker(600)
    picker.unit.setCurrentIndex(0)          # 秒
    assert picker.spin.value() == 10
    assert picker.value() == 10


def test_unit_switch_clamps_to_one_hour(qapp):
    """10 分钟切到「小时」等于 10 小时，超上限按 1 小时收住。"""
    picker = OfflineHidePicker(600)
    picker.unit.setCurrentIndex(2)          # 小时
    assert picker.value() == 3600
    assert picker.spin.value() == 1
    assert picker.unit.currentData() == 3600


def test_out_of_range_is_clamped(qapp):
    """超过 1 小时自动夹紧，不会写进非法值。"""
    picker = OfflineHidePicker(3600)
    picker.spin.setValue(3600)              # 3600 × 小时
    assert picker.value() == 3600
    assert picker.spin.value() == 1
    assert picker.unit.currentData() == 3600


def test_backfill_prefers_exact_unit(qapp):
    """90 秒不能被四舍五入成 1 分钟，也不能显示 1.5 分钟。"""
    picker = OfflineHidePicker(90)
    assert picker.value() == 90
    assert picker.spin.value() == 90
    assert picker.unit.currentData() == 1


def test_units_and_presets_cover_the_requested_range():
    assert [int(key) for key, _ in OFFLINE_UNITS] == [1, 60, 3600]
    assert sorted(int(key) for key, _ in OFFLINE_PRESETS) == [300, 600, 1800, 3600]
