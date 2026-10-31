"""托盘图标的文字对比度：浅色设备颜色下数字必须看得清。

以前数字写死白色，设备颜色选浅黄/浅绿/白色时等于白字白底，只剩一团糊。
现在按底色相对亮度自动切黑/白。
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QColor

from app.ui import theme
from app.ui.tray import FG_DARK, FG_LIGHT, battery_icon, foreground_on, relative_luminance

LIGHT_COLORS = ["#FFFFFF", "#FFE066", "#FFD60A", "#C7F5D8", "#F2F2F7"]
DARK_COLORS = ["#0A84FF", "#1C1C1E", "#2FC48A", "#FF453A", "#0B3D91"]


def icon_image(color: str, level: int = 80, show_percent: bool = True):
    icon = battery_icon(level, color, charging=False, show_percent=show_percent, offline=False)
    return icon.pixmap(48, 48).toImage()


def count_pixels(image, predicate) -> int:
    total = 0
    for y in range(image.height()):
        for x in range(image.width()):
            c = image.pixelColor(x, y)
            if c.alpha() < 40:
                continue
            if predicate(c):
                total += 1
    return total


def test_luminance_extremes():
    assert relative_luminance(QColor("#FFFFFF")) == pytest.approx(1.0, abs=0.01)
    assert relative_luminance(QColor("#000000")) == pytest.approx(0.0, abs=0.01)


@pytest.mark.parametrize("color", LIGHT_COLORS)
def test_light_background_uses_dark_text(color):
    assert foreground_on(QColor(color)) is FG_DARK or foreground_on(QColor(color)) == FG_DARK


@pytest.mark.parametrize("color", DARK_COLORS)
def test_dark_background_uses_white_text(color):
    assert foreground_on(QColor(color)) == FG_LIGHT


def test_white_icon_actually_has_dark_pixels():
    """纯白设备色：图标里必须出现深色像素（就是那串数字）。"""
    image = icon_image("#FFFFFF")
    dark = count_pixels(image, lambda c: relative_luminance(c) < 0.12)
    assert dark > 20, "白底上没画出深色数字，说明还是白字白底"


def test_dark_icon_keeps_white_text():
    image = icon_image("#0A84FF")
    bright = count_pixels(image, lambda c: relative_luminance(c) > 0.85)
    assert bright > 20, "深蓝底上的数字应仍是白色"


def test_offline_gray_keeps_white_text(qapp):
    """离线灰底偏深，仍用白字（不因这次改动误伤）。"""
    assert foreground_on(QColor(theme.TEXT_TERTIARY)) == FG_LIGHT
