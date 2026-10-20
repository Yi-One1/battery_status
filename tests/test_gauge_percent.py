"""电量仪表渲染测试：三种样式在电量可读时都要带 % 符号。"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication


pytestmark = pytest.mark.usefixtures("qapp")


def _capture_texts(monkeypatch) -> list[str]:
    captured: list[str] = []
    original_drawText = QPainter.drawText

    def fake_drawText(painter, *args):
        if len(args) == 3 and isinstance(args[2], str):
            captured.append(args[2])
        elif len(args) == 2 and isinstance(args[1], str):
            captured.append(args[1])
        return original_drawText(painter, *args)

    monkeypatch.setattr(QPainter, "drawText", fake_drawText)
    return captured


def _paint(style: str, level, charging: bool, monkeypatch) -> list[str]:
    from app.ui.gauge import BatteryGauge

    captured = _capture_texts(monkeypatch)
    gauge = BatteryGauge(style)
    gauge.resize(gauge.sizeHint())
    gauge.set_state(level, charging, True)
    pixmap = QPixmap(gauge.size())
    pixmap.fill(pixmap.toImage().pixelColor(0, 0))
    painter = QPainter(pixmap)
    gauge.paintEvent(None)
    painter.end()
    return captured


@pytest.mark.parametrize("style", ["ring", "bar", "number"])
def test_charging_with_readable_level_shows_percent(monkeypatch, style):
    """充电且电量可读（如 96%）：三种样式都要绘制 % 符号，与其它设备一致。"""
    captured = _paint(style, 96, True, monkeypatch)
    assert any("%" in text for text in captured), f"{style} 样式充电时缺 % 符号：{captured}"
    assert any("96" in text for text in captured), f"{style} 样式缺电量数字：{captured}"


@pytest.mark.parametrize("style", ["ring", "bar", "number"])
def test_not_charging_still_shows_percent(monkeypatch, style):
    captured = _paint(style, 46, False, monkeypatch)
    assert any("%" in text for text in captured), f"{style} 样式缺 % 符号：{captured}"


@pytest.mark.parametrize("style", ["ring", "bar", "number"])
def test_charging_placeholder_shows_bolt_not_number(monkeypatch, style):
    """充电占位符（电量不可读）：只画 ⚡，不画数字与 %。"""
    captured = _paint(style, None, True, monkeypatch)
    assert any("⚡" in text for text in captured), f"{style} 样式占位符缺闪电：{captured}"
    assert not any("%" in text for text in captured), f"{style} 样式占位符不应有 %：{captured}"
