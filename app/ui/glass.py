"""玻璃质感基元：柔光底、噪点、阴影，以及几个自绘控件。

全部自绘，不依赖图片资源，DPI 缩放与深浅色背景下都不会糊。
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QGraphicsDropShadowEffect,
    QSizePolicy,
    QWidget,
)

from . import theme

_NOISE: Optional[QPixmap] = None


def noise_pixmap() -> QPixmap:
    """64x64 微噪点，用于模拟磨砂表面的颗粒感。"""
    global _NOISE
    if _NOISE is not None:
        return _NOISE
    size = 64
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    seed = 20240517
    for y in range(size):
        for x in range(size):
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            alpha = (seed >> 16) % 7
            image.setPixelColor(x, y, QColor(255, 255, 255, alpha))
    _NOISE = QPixmap.fromImage(image)
    return _NOISE


def paint_refresh_icon(painter: QPainter, center: QPointF, radius: float, color: QColor) -> None:
    """画一个标准刷新图标：闭合感强的圆弧 + 贴在末端的切线箭头。

    几何要点（这是之前图标看起来"不圆"的根因）：
    - 缺口固定 45°，位于右上（37.5°~82.5°），弧覆盖其余 315°，视觉上是完整圆环；
    - 箭头三角按弧末端的切线方向摆放，底边跨在弧线上，而不是另外画两条短线；
    - 箭头长度、宽度与线宽成比例，缩放到 16px 也不会糊成一团。
    """
    start, span = 32.5, -305.0
    rect = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
    arc = QPainterPath()
    arc.arcMoveTo(rect, start)
    arc.arcTo(rect, start, span)
    painter.drawPath(arc)

    end = math.radians(start + span)
    ex, ey = math.cos(end), -math.sin(end)
    point = QPointF(center.x() + radius * ex, center.y() + radius * ey)
    # 顺时针旋转的切线方向
    tx, ty = math.cos(end - math.pi / 2), -math.sin(end - math.pi / 2)
    length = radius * 0.50
    half = radius * 0.16
    base = QPointF(point.x() - tx * length * 0.12, point.y() - ty * length * 0.12)
    tip = QPointF(point.x() + tx * length, point.y() + ty * length)
    wing_a = QPointF(base.x() + ex * half, base.y() + ey * half)
    wing_b = QPointF(base.x() - ex * half, base.y() - ey * half)

    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawPolygon(QPolygonF([tip, wing_a, wing_b]))
    painter.restore()


def rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def paint_glass(
    painter: QPainter,
    rect: QRectF,
    radius: float,
    *,
    alpha: float = 0.80,
    tint: Optional[str] = None,
    tint_alpha: float = 0.10,
    border_alpha: float = 0.75,
    glow: bool = True,
) -> None:
    """绘制一块柔光玻璃：纵向渐变底 + 左上柔光 + 噪点 + 高光描边。"""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = rounded_path(rect, radius)

    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    gradient.setColorAt(0.0, QColor(255, 255, 255, int(255 * min(1.0, alpha + 0.08))))
    gradient.setColorAt(0.55, QColor(252, 253, 255, int(255 * alpha)))
    gradient.setColorAt(1.0, QColor(243, 245, 250, int(255 * max(0.0, alpha - 0.06))))
    painter.fillPath(path, QBrush(gradient))

    if tint:
        tint_gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        tint_gradient.setColorAt(0.0, theme.color(tint, tint_alpha))
        tint_gradient.setColorAt(1.0, theme.color(tint, 0.0))
        painter.fillPath(path, QBrush(tint_gradient))

    if glow:
        radial = QRadialGradient(
            QPointF(rect.left() + rect.width() * 0.22, rect.top() - rect.height() * 0.12),
            rect.width() * 1.05,
        )
        radial.setColorAt(0.0, QColor(255, 255, 255, 120))
        radial.setColorAt(0.55, QColor(255, 255, 255, 26))
        radial.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillPath(path, QBrush(radial))

    painter.save()
    painter.setClipPath(path)
    painter.setOpacity(0.5)
    painter.fillRect(rect, QBrush(noise_pixmap()))
    painter.restore()

    # 外描边（自上而下由亮到几乎透明，制造玻璃厚度）
    border = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    border.setColorAt(0.0, QColor(255, 255, 255, int(255 * border_alpha)))
    border.setColorAt(0.45, QColor(255, 255, 255, int(255 * border_alpha * 0.45)))
    border.setColorAt(1.0, QColor(255, 255, 255, int(255 * border_alpha * 0.30)))
    painter.strokePath(path, QPen(QBrush(border), 1.0))

    hairline = QPen(QColor(28, 28, 30, 16), 1.0)
    painter.strokePath(rounded_path(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius), hairline)
    painter.restore()


def apply_shadow(widget: QWidget, blur: int = 48, dy: int = 16, alpha: int = 46) -> None:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    effect.setColor(QColor(17, 24, 39, alpha))
    widget.setGraphicsEffect(effect)


class GlassSurface(QWidget):
    """一块可复用的玻璃面板。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        radius: float = 18.0,
        alpha: float = 0.58,
        tint: Optional[str] = None,
        tint_alpha: float = 0.0,
        interactive: bool = False,
    ) -> None:
        super().__init__(parent)
        self.radius = radius
        self.base_alpha = alpha
        self.tint = tint
        self.tint_alpha = tint_alpha
        self.interactive = interactive
        self.hovered = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        if interactive:
            self.setMouseTracking(True)

    def enterEvent(self, event) -> None:  # noqa: N802
        if self.interactive:
            self.hovered = True
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self.interactive:
            self.hovered = False
            self.update()
        super().leaveEvent(event)

    def set_tint(self, tint: Optional[str], tint_alpha: float = 0.10) -> None:
        self.tint = tint
        self.tint_alpha = tint_alpha
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        alpha = self.base_alpha + (0.10 if self.hovered else 0.0)
        paint_glass(
            painter,
            rect,
            self.radius,
            alpha=min(0.95, alpha),
            tint=self.tint,
            tint_alpha=self.tint_alpha,
        )
        painter.end()


class IconButton(QAbstractButton):
    """矢量图标按钮，悬停时浮现圆形底色。"""

    def __init__(
        self,
        kind: str,
        parent: Optional[QWidget] = None,
        *,
        diameter: int = 28,
        color: str = theme.TEXT_SECONDARY,
    ) -> None:
        super().__init__(parent)
        self.kind = kind
        self.diameter = diameter
        self.color = color
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(diameter, diameter)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # 按钮浮在玻璃上，自身不能刷不透明底色
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        if self._hover or self.isDown():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(28, 28, 30, 22 if not self.isDown() else 34))
            painter.drawEllipse(rect)
        pen = QPen(QColor(self.color if not self.isDown() else theme.TEXT_PRIMARY), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        c = rect.center()
        r = rect.width() / 2
        s = r * 0.42
        if self.kind == "refresh":
            paint_refresh_icon(painter, c, r * 0.63, QColor(pen.color()))
        elif self.kind in ("gear", "sliders"):
            # 三根滑杆，比齿轮更清爽，也更像系统设置图标
            for slot, knob in ((-0.62, 0.42), (0.0, -0.4), (0.62, 0.18)):
                y = c.y() + slot * s
                painter.drawLine(QPointF(c.x() - s * 0.95, y), QPointF(c.x() + s * 0.95, y))
                painter.setBrush(QColor(255, 255, 255, 240))
                painter.drawEllipse(QPointF(c.x() + knob * s, y), s * 0.28, s * 0.28)
                painter.setBrush(Qt.BrushStyle.NoBrush)
        elif self.kind == "close":
            painter.drawLine(QPointF(c.x() - s * 0.72, c.y() - s * 0.72), QPointF(c.x() + s * 0.72, c.y() + s * 0.72))
            painter.drawLine(QPointF(c.x() + s * 0.72, c.y() - s * 0.72), QPointF(c.x() - s * 0.72, c.y() + s * 0.72))
        elif self.kind == "back":
            path = QPainterPath()
            path.moveTo(QPointF(c.x() + s * 0.45, c.y() - s * 0.8))
            path.lineTo(QPointF(c.x() - s * 0.35, c.y()))
            path.lineTo(QPointF(c.x() + s * 0.45, c.y() + s * 0.8))
            painter.drawPath(path)
        elif self.kind == "pin":
            painter.drawEllipse(c, s * 0.55, s * 0.55)
            painter.drawEllipse(c, s * 0.95, s * 0.95)
        painter.end()


class ToggleSwitch(QAbstractButton):
    """iOS 风格开关，带位移动画。"""

    def __init__(self, parent: Optional[QWidget] = None, *, width: int = 42, height: int = 24) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(width, height)
        self._offset = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.toggled.connect(self._animate)

    def _on_anim(self, value) -> None:
        self._offset = float(value)
        self.update()

    def _animate(self, checked: bool) -> None:
        start = self._offset
        end = 1.0 if checked else 0.0
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.start()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        radius = rect.height() / 2
        off = QColor(120, 120, 128, 60)
        on = QColor(theme.ACCENT_BLUE)
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._offset),
            int(off.green() + (on.green() - off.green()) * self._offset),
            int(off.blue() + (on.blue() - off.blue()) * self._offset),
            int(off.alpha() + (255 - off.alpha()) * self._offset),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, radius, radius)

        knob = rect.height() - 4
        x = rect.left() + 2 + self._offset * (rect.width() - knob - 4)
        painter.setBrush(QColor(255, 255, 255))
        painter.setPen(QPen(QColor(0, 0, 0, 12), 0.8))
        painter.drawEllipse(QRectF(x, rect.top() + 2, knob, knob))
        painter.end()


class SegmentedControl(QWidget):
    """分段选择器（Apple Segmented Control 观感）。"""

    changed = Signal(str)

    def __init__(self, options: Iterable[tuple[str, str]], parent: Optional[QWidget] = None, *, height: int = 26) -> None:
        super().__init__(parent)
        self._options = list(options)
        self._value = self._options[0][0] if self._options else ""
        self._index = 0
        self._hover_index = -1
        self.setFixedHeight(height)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str, emit: bool = False) -> None:
        for index, (key, _) in enumerate(self._options):
            if key == value:
                self._index = index
                self._value = key
                self.update()
                if emit:
                    self.changed.emit(key)
                return

    def _segment_rect(self, index: int) -> QRectF:
        count = max(1, len(self._options))
        width = self.width() / count
        return QRectF(index * width, 0, width, self.height())

    def mousePressEvent(self, event) -> None:  # noqa: N802
        for index, (key, _) in enumerate(self._options):
            if self._segment_rect(index).contains(event.position()):
                if key != self._value:
                    self._index = index
                    self._value = key
                    self.update()
                    self.changed.emit(key)
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        hover = -1
        for index, _ in enumerate(self._options):
            if self._segment_rect(index).contains(event.position()):
                hover = index
                break
        if hover != self._hover_index:
            self._hover_index = hover
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_index = -1
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(120, 120, 128, 26))
        painter.drawRoundedRect(rect, radius, radius)

        selected = self._segment_rect(self._index).adjusted(2, 2, -2, -2)
        painter.setBrush(QColor(255, 255, 255, 245))
        painter.setPen(QPen(QColor(0, 0, 0, 14), 0.8))
        painter.drawRoundedRect(selected, radius - 2, radius - 2)

        painter.setFont(theme.font(11.5, 500))
        for index, (_, label) in enumerate(self._options):
            cell = self._segment_rect(index)
            painter.setPen(QColor(theme.TEXT_PRIMARY if index == self._index else theme.TEXT_SECONDARY))
            painter.drawText(cell, Qt.AlignmentFlag.AlignCenter, label)
        painter.end()


class ColorSwatch(QAbstractButton):
    """颜色圆点，点击由外部接管（弹系统取色器）。"""

    def __init__(self, value: str, parent: Optional[QWidget] = None, *, diameter: int = 22) -> None:
        super().__init__(parent)
        self.value = value
        self.diameter = diameter
        self.setFixedSize(diameter, diameter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_color(self, value: str) -> None:
        self.value = value
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color(self.value, 0.22))
        painter.drawEllipse(rect.adjusted(-1, -1, 1, 1))
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, theme.lighten(self.value, 0.28))
        gradient.setColorAt(1.0, QColor(self.value))
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(rect)
        painter.setPen(QPen(QColor(255, 255, 255, 200), 1.0))
        painter.drawEllipse(rect.adjusted(0.5, 0.5, -0.5, -0.5))
        painter.end()


class Divider(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(60, 60, 67, 26))
        painter.end()
