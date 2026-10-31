"""断连收起时长控件测试：1 秒 ~ 1 小时可精确选择。

控件形态：数值输入 + 单位选择（QToolButton + QMenu），一行到底，没有预设档位。
"""
from __future__ import annotations

import pytest

from app.ui.settings import OFFLINE_UNITS, OfflineHidePicker


@pytest.fixture()
def picker(qapp):
    return OfflineHidePicker(600)


def unit_key(picker: OfflineHidePicker) -> str:
    return picker._unit_key


def test_default_shows_ten_minutes(picker):
    """600 秒应回填成「10 分钟」而不是 600 秒。"""
    assert picker.value() == 600
    assert picker.spin.value() == 10
    assert unit_key(picker) == "60"
    assert picker.unit_button.text() == "分钟"


def test_accepts_one_second(qapp):
    """下限 1 秒：以前最小只有 1 分钟，现在能选到秒级。"""
    picker = OfflineHidePicker(1)
    assert picker.value() == 1
    assert picker.spin.value() == 1
    assert picker.unit_button.text() == "秒"


def test_spin_changes_emit_seconds(qapp):
    picker = OfflineHidePicker(600)
    seen: list[int] = []
    picker.changed.connect(seen.append)

    picker.spin.setValue(3)          # 3 分钟 = 180 秒
    assert picker.value() == 180
    assert seen == [180]


def test_unit_menu_sets_unit(qapp):
    picker = OfflineHidePicker(600)
    seen: list[int] = []
    picker.changed.connect(seen.append)

    picker._on_unit("3600")          # 10 小时 → 夹到 1 小时
    assert picker.value() == 3600
    assert picker.spin.value() == 1
    assert picker.unit_button.text() == "小时"
    assert seen == [3600]


def test_unit_switch_keeps_number(qapp):
    """数值 + 单位是一个整体输入：10 分钟切到「秒」就是 10 秒，不是 600 秒。"""
    picker = OfflineHidePicker(600)
    picker._on_unit("1")
    assert picker.spin.value() == 10
    assert picker.value() == 10
    assert picker.unit_button.text() == "秒"


def test_out_of_range_is_clamped(qapp):
    """超过 1 小时自动夹紧，不会写进非法值。"""
    picker = OfflineHidePicker(3600)
    picker.spin.setValue(3600)              # 3600 小时
    assert picker.value() == 3600
    assert picker.spin.value() == 1
    assert picker.unit_button.text() == "小时"


def test_backfill_prefers_exact_unit(qapp):
    """90 秒不能被四舍五入成 1 分钟，也不能显示 1.5 分钟。"""
    picker = OfflineHidePicker(90)
    assert picker.value() == 90
    assert picker.spin.value() == 90
    assert picker.unit_button.text() == "秒"


def test_layout_is_single_row_without_blank(picker):
    """一行布局：控件总宽就是内容宽，右侧不额外留白。"""
    picker.adjustSize()
    hint = picker.sizeHint().width()
    assert picker.height() <= 30, "只剩一行，不该再占两行高度"
    assert hint == picker.spin.width() + picker.unit_button.width() + 6
    assert not hasattr(picker, "presets"), "四个预设档位已移除"


def test_number_is_centered(picker):
    """数值居中，与单位框文字对齐（以前一右一左）。"""
    from PySide6.QtCore import Qt

    assert picker.spin.alignment() & Qt.AlignmentFlag.AlignHCenter


def test_unit_menu_has_three_options(picker):
    assert [a.text() for a in picker.unit_menu.actions()] == [label for _, label in OFFLINE_UNITS]


def test_units_cover_the_requested_range():
    assert [int(key) for key, _ in OFFLINE_UNITS] == [1, 60, 3600]
