"""托盘图标渲染测试。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap


def test_battery_icon_with_percent_draws_percent_sign(monkeypatch):
    """图标内显示百分比时，绘制的文本必须包含 % 符号。"""
    from PySide6.QtWidgets import QApplication

    from app.ui.tray import _draw_icon

    # PySide6 绘图前需要 QApplication 实例
    if QApplication.instance() is None:
        QApplication(["test"])

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
