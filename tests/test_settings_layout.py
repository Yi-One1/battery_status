"""设置页布局测试：长副标题不得把右侧控件推出边界。"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLabel, QWidget

from app.core.config import DeviceRegistry, SettingsStore


pytestmark = pytest.mark.usefixtures("qapp")


def _right_edges(parent: QWidget) -> list[int]:
    """parent 内所有可见 QLabel 右边缘映射到 parent 坐标的 x 值。"""
    edges = []
    for label in parent.findChildren(QLabel):
        if not label.isVisible() or not label.text():
            continue
        edges.append(label.mapTo(parent, label.rect().topRight()).x())
    return edges


def test_setting_row_long_subtitle_stays_inside(qapp):
    """副标题过长时自动换行，任何标签都不能越过行右边界。"""
    from app.ui.glass import SegmentedControl
    from app.ui.settings import SettingRow

    row = SettingRow(
        "窗口背景",
        "柔光玻璃跟随桌面；纯白色最省电；高斯模糊取桌面并加重模糊" * 2,
        SegmentedControl([("a", "选项A"), ("b", "选项B")], height=24),
    )
    row.setFixedWidth(380)
    row.show()
    qapp.processEvents()

    edges = _right_edges(row)
    assert edges, "行内没有找到任何标签"
    overflow = [e for e in edges if e > row.width()]
    assert not overflow, f"标签越过右边界：{overflow} > {row.width()}"


def test_settings_pane_fits_panel_width(qapp, tmp_path):
    """整个设置页在 414px 视口宽度内，任何标签都不越出右边界。"""
    from app.ui.settings import SettingsPane

    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    pane = SettingsPane(registry, store)
    pane.setFixedWidth(414)
    pane.resize(414, 700)
    pane.show()
    qapp.processEvents()

    edges = _right_edges(pane)
    assert edges, "设置页没有找到任何标签"
    overflow = [e for e in edges if e > pane.width()]
    assert not overflow, f"标签越过设置页右边界：{overflow} > {pane.width()}"
