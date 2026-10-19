"""图标几何测试：用像素级指标锁住"刷新图标必须是正圆"这件事。

这些断言不是"截图对比"那种一改就红的形式测试，而是直接测量外轮廓的径向分布：
只要圆弧半径缩水、缺口跑到别处、箭头飞出圆外，指标立刻不达标。
"""
from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from app.ui.glass import IconButton
from tests.conftest import (
    band_coverage,
    bbox,
    gap_angles,
    ink_points,
    longest_gap,
    quadrant_coverage,
    radial_profile,
)
from tools.render_icons import render_icon

SIZES = (16, 20, 28, 32, 40)
SCALE = 6


def _measure(kind: str, size: int = 28, scale: int = SCALE, color: str = "#5B5F6B"):
    image = render_icon(kind, color, scale=scale, size=size)
    points = ink_points(image)
    profile = radial_profile(points, image.width(), image.height())
    return image, points, profile


def test_refresh_icon_is_centered(qapp):
    for size in SIZES:
        _, points, _ = _measure("refresh", size)
        assert points, f"{size}px 下没有画出任何内容"
        left, top, right, bottom = bbox(points)
        center_x = (left + right) / 2 / SCALE
        center_y = (top + bottom) / 2 / SCALE
        assert abs(center_x - size / 2) <= 1.2, f"{size}px 横向偏移 {center_x - size / 2:.2f}"
        assert abs(center_y - size / 2) <= 1.2, f"{size}px 纵向偏移 {center_y - size / 2:.2f}"


def test_refresh_arc_is_full_sized_circle(qapp):
    """圆弧必须撑满按钮，而不是缩在中间的小圈（旧版半径只有 0.42R）。"""
    for size in SIZES:
        _, _, profile = _measure("refresh", size)
        ring = sorted(value for value in profile if 0.55 <= value <= 0.80)
        assert ring, f"{size}px 找不到环带"
        median = ring[len(ring) // 2]
        assert 0.58 <= median <= 0.72, f"{size}px 环带中位半径 {median:.3f}，不像撑满的圆"


def test_refresh_outline_stays_on_the_circle(qapp):
    """外轮廓覆盖率：360° 里至少 90% 的角度有墨迹落在同一圈上。"""
    for size in SIZES:
        _, _, profile = _measure("refresh", size)
        coverage = band_coverage(profile)
        assert coverage >= 0.90, f"{size}px 环形覆盖只有 {coverage:.3f}，轮廓不圆"


def test_refresh_gap_sits_top_right_and_stays_small(qapp):
    """缺口必须落在右上，且宽度合理（旧版缺口 75° 明显过大）。"""
    for size in SIZES:
        _, _, profile = _measure("refresh", size)
        start, width = longest_gap(profile)
        assert width > 0, f"{size}px 缺口消失了，箭头把整圈填满"
        assert 12 <= width <= 60, f"{size}px 缺口 {width:.0f}°，比例失衡"
        assert 25 <= start <= 60, f"{size}px 缺口起点 {start:.0f}°，不在右上"
        assert start + width <= 100, f"{size}px 缺口蔓延到 {start + width:.0f}°"
        # 缺口只应有一处：碎片化说明轮廓有断口或折角
        assert len(gap_angles(profile)) <= width * 1.6 + 6, f"{size}px 缺口碎裂，轮廓不连续"


def test_refresh_arrow_present_and_inside_bounds(qapp):
    """箭头必须存在（缺口区域有墨迹），但不能飞到圆外。"""
    for size in SIZES:
        _, _, profile = _measure("refresh", size)
        arrow = max(profile[78:100])
        assert arrow >= 0.58, f"{size}px 缺口处没有箭头（最大半径 {arrow:.2f}）"
        assert max(profile) <= 0.85, f"{size}px 箭头明显溢出圆外（{max(profile):.2f}）"


def test_refresh_three_quadrants_are_even(qapp):
    """除缺口所在的右上象限外，其余三个象限应当同样饱满。"""
    _, _, profile = _measure("refresh", 28)
    coverage = quadrant_coverage(profile)
    for name in ("左上", "左下", "右下"):
        assert coverage[name] >= 0.97, f"{name}象限覆盖率 {coverage[name]:.2f}"
    assert coverage["右上"] < coverage["左上"], "缺口应当在右上，而不是别处"


def test_refresh_does_not_fill_the_button(qapp):
    total = qapp.primaryScreen().size()  # 触发一次屏幕查询，确保平台插件就绪
    assert total.width() > 0
    image, points, _ = _measure("refresh", 40, scale=4)
    ratio = len(points) / (image.width() * image.height())
    assert 0.03 <= ratio <= 0.40, f"墨迹占比 {ratio:.3f} 异常"


def test_buttons_are_transparent_over_glass(qapp):
    """按钮自身不能刷不透明底色，否则会透过一块白方块。"""
    for kind in ("refresh", "sliders", "close", "back", "gear"):
        button = IconButton(kind, diameter=28)
        assert button.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        image = render_icon(kind, "#5B5F6B")
        corner = image.pixelColor(1, 1)
        assert corner.alpha() == 0, f"{kind} 图标四角不透明（alpha={corner.alpha()}）"


def test_all_icon_kinds_draw_something(qapp):
    for kind in ("refresh", "sliders", "close", "back", "gear", "pin"):
        image, points, _ = _measure(kind, 28)
        assert len(points) > 200, f"{kind} 图标几乎是空的"


# --------------------------------------------------------------------------- #
# 对照实验：把旧版的画法原样复刻一遍，确认本测试确实能抓住"不圆"的图标。
# 如果这个测试不再失败，说明断言已经松弛到没有判别力了。
# --------------------------------------------------------------------------- #
def _render_legacy_refresh(size: int = 28, scale: int = SCALE) -> QImage:
    image = QImage(size * scale, size * scale, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(scale, scale)
    painter.translate(size / 2, size / 2)
    pen = QPen(QColor("#5B5F6B"), 1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    s = size / 2 * 0.42
    path = QPainterPath()
    path.arcMoveTo(QRectF(-s, -s, s * 2, s * 2), 60)
    path.arcTo(QRectF(-s, -s, s * 2, s * 2), 60, 285)
    painter.drawPath(path)
    tip = QPointF(s * 0.98, -s * 0.62)
    painter.drawLine(tip, QPointF(tip.x() - s * 0.52, tip.y() - s * 0.06))
    painter.drawLine(tip, QPointF(tip.x() + s * 0.02, tip.y() + s * 0.48))
    painter.end()
    return image


def test_legacy_geometry_is_rejected(qapp):
    image = _render_legacy_refresh()
    points = ink_points(image)
    profile = radial_profile(points, image.width(), image.height())
    ring = sorted(value for value in profile if 0.30 <= value <= 0.60)
    assert ring, "对照实验本身画失败"
    median = ring[len(ring) // 2]
    gap = gap_angles(profile, low=0.30)
    too_small = median < 0.58
    gap_too_wide = len(gap) > 60 or any(angle > 95 for angle in gap)
    assert too_small or gap_too_wide, "旧几何居然通过了新标准，说明断言失效"
