"""托盘图标渲染测试。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module", autouse=True)
def _ensure_qapp():
    if QApplication.instance() is None:
        QApplication(["test"])


def test_battery_icon_with_percent_draws_percent_sign(monkeypatch):
    """图标内显示百分比时，绘制的文本必须包含 % 符号。"""
    from app.ui.tray import _draw_icon

    captured: list[str] = []
    original_drawText = QPainter.drawText

    def fake_drawText(painter, *args):
        # drawText(QRectF, int, str) 或 drawText(QPointF, str)
        if len(args) == 3 and isinstance(args[2], str):
            captured.append(args[2])
        elif len(args) == 2 and isinstance(args[1], str):
            captured.append(args[1])
        return original_drawText(painter, *args)

    monkeypatch.setattr(QPainter, "drawText", fake_drawText)

    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    _draw_icon(painter, 32, 83, "#0A84FF", False, True, False)
    painter.end()

    assert any("%" in text for text in captured), f"绘制的文本不含 %：{captured}"
    assert any("83" in text for text in captured), f"绘制的文本不含电量数字：{captured}"


def test_device_line_uses_display_level():
    """充电占位符（level=50 & charging）时，托盘提示应隐藏电量而不是显示 50%。"""
    from app.core.config import DeviceRegistry, SettingsStore
    from app.core.model import Snapshot
    from app.ui.tray import TrayController

    registry = DeviceRegistry()
    store = SettingsStore(registry)
    tray = TrayController(registry, store)

    placeholder = Snapshot(
        device_id="mchose-g98pro",
        level=50,
        charging=True,
        online=True,
        link="wireless",
    )
    line = tray._device_line("迈从 G98 PRO", placeholder)
    assert "50%" not in line
    assert "--" in line or "充电中" in line

    normal = Snapshot(
        device_id="mchose-g98pro",
        level=83,
        charging=False,
        online=True,
        link="wireless",
    )
    line = tray._device_line("迈从 G98 PRO", normal)
    assert "83%" in line
