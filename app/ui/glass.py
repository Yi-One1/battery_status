"""玻璃质感基元：柔光底、噪点、阴影，以及几个自绘控件。

全部自绘，不依赖图片资源，DPI 缩放与深浅色背景下都不会糊。

"柔光玻璃"的实现要点（对应澎湃 OS 4 的那套材质语言）：
  * **感知融色** —— 面板显示前把窗口背后的桌面截下来（backdrop.py），
    重度模糊后当玻璃的底，再从里面取一个降饱和的环境色铺一层，
    于是换壁纸整个界面的调子跟着变；
  * **自适应通透度** —— 背景越暗，白纱越厚，保证深色壁纸下深色字依然清楚；
  * **光影秩序** —— 上缘一道亮边、下缘一道更细的反光线、内侧一圈极淡的暗收口，
    靠这三笔把"平板"画成"有厚度的一块玻璃"；
  * **点按有光** —— 悬停时玻璃自己提亮一点，并浮出所属颜色的柔光。
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
    QFontMetrics,
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
from .backdrop import DEFAULT_PALETTE, BackdropPalette

_NOISE: Optional[QPixmap] = None

# --------------------------------------------------------------------------- #
# 当前背景（由 Panel 在显示前设置，所有玻璃控件共享）
#
# 底图是**整屏**的板材，自带原点与缩放：
#   * 控件用屏幕全局坐标去取像素，所以面板拖到哪，玻璃透出的就是那块桌面；
#   * 每隔几秒重新取一次，换壁纸也会跟上；
#   * 没有背景板时（截图失败）origin 为 (0,0)、scale 为 1，退化成"窗口局部"语义。
# --------------------------------------------------------------------------- #
_BACKDROP: Optional[QPixmap] = None
_PALETTE: BackdropPalette = DEFAULT_PALETTE
_BACKDROP_ORIGIN = QPoint(0, 0)
_BACKDROP_SCALE = 1.0


def set_backdrop(
    pixmap: Optional[QPixmap],
    palette: Optional[BackdropPalette] = None,
    *,
    origin: Optional[QPoint] = None,
    scale: float = 1.0,
) -> None:
    """设置"窗口背后的桌面"。

    pixmap 与窗口等大时用默认的 origin=(0,0)/scale=1（窗口局部坐标）；
    整屏取样时把屏幕原点和 SCALE 一起传进来，绘制端就能按全局坐标对齐。
    """
    global _BACKDROP, _PALETTE, _BACKDROP_ORIGIN, _BACKDROP_SCALE
    _BACKDROP = pixmap
    _PALETTE = palette or DEFAULT_PALETTE
    _BACKDROP_ORIGIN = QPoint(origin) if origin is not None else QPoint(0, 0)
    _BACKDROP_SCALE = max(0.01, float(scale or 1.0))


def clear_backdrop() -> None:
    set_backdrop(None, None)


def current_backdrop() -> Optional[QPixmap]:
    return _BACKDROP


def backdrop_origin() -> QPoint:
    return QPoint(_BACKDROP_ORIGIN)


def backdrop_scale() -> float:
    return _BACKDROP_SCALE


def environment_palette() -> BackdropPalette:
    return _PALETTE


def environment_color() -> QColor:
    return QColor(_PALETTE.color)


# 白纱自适应的几个拐点
SCRIM_FLOOR_RATIO = 0.42   # 亮背景下的通透度 = 传入 alpha × 这个比例
SCRIM_CEILING = 0.78       # 最暗背景下的白纱上限（保证深色文字仍然清楚）
COMFORT_LUMA = 0.62        # 背景亮到这一步就不再额外加纱
LUMA_RANGE = 0.50          # 从 COMFORT_LUMA 往下多少亮度区间走满整条曲线


def adaptive_scrim(base_alpha: float) -> float:
    """感知内容自适应通透度：亮背景几乎不加纱，暗背景加厚到能压住深色文字。

    这是"柔光玻璃"和"白色半透明塑料板"的分界线。老实现是
    `base_alpha × (0.64 + 0.42 × darkness)`，亮背景仍有 0.5 以上的白纱，
    壁纸的颜色全被洗掉，看上去就是一块磨砂白板。现在改成两段式：
    亮背景只留 `base_alpha × 0.42`（让颜色透上来），越往暗处按 1.25 次幂抬到 0.78。

    没有背景图时直接返回调用方给的不透明度，保持老行为。
    """
    if _BACKDROP is None:
        return base_alpha
    luma = max(0.0, min(1.0, _PALETTE.luminance))
    darkness = max(0.0, min(1.0, (COMFORT_LUMA - luma) / LUMA_RANGE))
    floor = max(0.14, min(1.0, base_alpha * SCRIM_FLOOR_RATIO))
    ceiling = max(floor, SCRIM_CEILING)
    return floor + (ceiling - floor) * (darkness ** 1.25)



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


def paint_specular(painter: QPainter, rect: QRectF, radius: float, *, border_alpha: float = 0.75) -> None:
    """玻璃的"厚度"三笔：上缘亮边、下缘反光线、内侧暗收口。

    少任何一笔都会立刻变回"一张半透明的平面图"：
    上缘亮边交代光从哪来，下缘反光交代材质有多厚，内侧暗边把玻璃和背景切开。
    """
    outer = rounded_path(rect, radius)
    border = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    border.setColorAt(0.0, QColor(255, 255, 255, int(255 * border_alpha)))
    border.setColorAt(0.40, QColor(255, 255, 255, int(255 * border_alpha * 0.40)))
    border.setColorAt(0.84, QColor(255, 255, 255, int(255 * border_alpha * 0.26)))
    border.setColorAt(1.0, QColor(255, 255, 255, int(255 * border_alpha * 0.74)))
    painter.strokePath(outer, QPen(QBrush(border), 1.0))

    inner = rounded_path(rect.adjusted(0.7, 0.7, -0.7, -0.7), max(1.0, radius - 0.7))
    fade = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    fade.setColorAt(0.0, QColor(28, 28, 30, 0))
    fade.setColorAt(0.72, QColor(28, 28, 30, 8))
    fade.setColorAt(1.0, QColor(28, 28, 30, 26))
    painter.strokePath(inner, QPen(QBrush(fade), 1.0))


def paint_glass(
    painter: QPainter,
    rect: QRectF,
    radius: float,
    *,
    alpha: float = 0.80,
    tint: Optional[str] = None,
    tint_alpha: float = 0.12,
    border_alpha: float = 0.75,
    glow: bool = True,
    offset: Optional[QPointF] = None,
    backdrop_weight: float = 1.0,
    solid: bool = False,
) -> None:
    """绘制一块柔光玻璃。

    offset            本控件在**窗口坐标系**里的左上角，用来从背景图上取对应区域
    backdrop_weight   背景图透出的强度：外壳 1.0，叠在外壳上的卡片用 0.3 左右
                      （否则卡片会把自己下面的桌面再画一遍，看起来比周围更暗）；
                      传 0 表示完全不参与背景取样（对话框这类独立窗口用），
                      此时白纱也不再自适应，按传入的 alpha 原样铺 —— 表单必须读得清。
    solid             纯白不透明模式：跳过白纱/噪点/柔光等玻璃笔触，整块填实心白。
                      白纱渐变的边缘只有 ~0.58 不透明度，靠调 alpha 到不了全不透明。
    """
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = rounded_path(rect, radius)
    if solid:
        painter.fillPath(path, QColor(255, 255, 255, 255))
        paint_specular(painter, rect, radius, border_alpha=border_alpha)
        painter.restore()
        return
    backdrop = _BACKDROP
    palette = _PALETTE
    show_backdrop = backdrop is not None and not backdrop.isNull() and backdrop_weight > 0.0
    # ① 背后的桌面（已重度模糊）
    if show_backdrop:
        origin = offset or QPointF(0.0, 0.0)
        # 控件给的是**屏幕全局坐标**，换算成板材像素坐标
        source = QRectF(
            (origin.x() - _BACKDROP_ORIGIN.x()) / _BACKDROP_SCALE,
            (origin.y() - _BACKDROP_ORIGIN.y()) / _BACKDROP_SCALE,
            rect.width() / _BACKDROP_SCALE,
            rect.height() / _BACKDROP_SCALE,
        )
        # 背景板比控件小的时候（缩放出岔子、HiDPI 换算错位），越界取样会让整块背景
        # 直接消失。裁剪出有效区域再按比例映射回目标矩形，至少不会突然变成一张白纸。
        valid = source.intersected(QRectF(0.0, 0.0, float(backdrop.width()), float(backdrop.height())))
        if not valid.isEmpty():
            kx = rect.width() / max(1e-6, source.width())
            ky = rect.height() / max(1e-6, source.height())
            dest = QRectF(
                rect.left() + (valid.left() - source.left()) * kx,
                rect.top() + (valid.top() - source.top()) * ky,
                valid.width() * kx,
                valid.height() * ky,
            )
            painter.save()
            painter.setClipPath(path)
            painter.setOpacity(max(0.0, min(1.0, backdrop_weight)))
            # 板材是 1/SCALE 的，放大时要平滑，否则会看到块状边界
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawPixmap(dest, backdrop, valid)
            painter.restore()

    # ② 白纱：中心厚、四边薄 —— 这就是"边缘融色"，澎湃那套玻璃最认的指纹
    #
    # 一片均匀的白纱会盖成一堵白墙；改成径向，中心保内容可读，四周薄下去，
    # 壁纸的颜色就从边缘渗进来，一眼能看出这是一块"透着的"玻璃。
    scrim = adaptive_scrim(alpha) if show_backdrop else alpha
    edge = max(0.12, scrim * 0.58)
    veil = QRadialGradient(
        QPointF(rect.center().x(), rect.center().y() - rect.height() * 0.06),
        max(rect.width(), rect.height()) * 0.82,
    )
    veil.setColorAt(0.0, QColor(255, 255, 255, int(255 * min(1.0, scrim + 0.06))))
    veil.setColorAt(0.56, QColor(253, 254, 255, int(255 * scrim)))
    veil.setColorAt(1.0, QColor(246, 248, 252, int(255 * edge)))
    painter.fillPath(path, QBrush(veil))

    # ③ 顶部受光：一条很薄的亮带，交代光从上边来
    top_light = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + rect.height() * 0.34))
    top_light.setColorAt(0.0, QColor(255, 255, 255, 66))
    top_light.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillPath(path, QBrush(top_light))

    # ④ 环境色：从背景里借来的调子，斜向铺开，让四个角不完全一样
    if show_backdrop and palette.saturation > 0.02:
        env_gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        env_gradient.setColorAt(0.0, theme.color(palette.color.name(), min(0.22, palette.saturation * 0.75)))
        env_gradient.setColorAt(0.55, theme.color(palette.color.name(), 0.05))
        env_gradient.setColorAt(1.0, theme.color(palette.color.name(), 0.0))
        painter.fillPath(path, QBrush(env_gradient))

    # ⑤ 类型色：卡片用所属设备的颜色轻轻染一层
    if tint:
        tint_gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        tint_gradient.setColorAt(0.0, theme.color(tint, tint_alpha))
        tint_gradient.setColorAt(1.0, theme.color(tint, 0.0))
        painter.fillPath(path, QBrush(tint_gradient))

    # ⑥ 左上柔光（点按有光的那束光）
    if glow:
        radial = QRadialGradient(
            QPointF(rect.left() + rect.width() * 0.22, rect.top() - rect.height() * 0.12),
            rect.width() * 1.05,
        )
        radial.setColorAt(0.0, QColor(255, 255, 255, 96))
        radial.setColorAt(0.55, QColor(255, 255, 255, 24))
        radial.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillPath(path, QBrush(radial))

    # ⑦ 磨砂颗粒：只留一点点，柔光玻璃是"润"的不是"砂"的
    painter.save()
    painter.setClipPath(path)
    painter.setOpacity(0.12)
    painter.fillRect(rect, QBrush(noise_pixmap()))
    painter.restore()

    # ⑧ 厚度
    paint_specular(painter, rect, radius, border_alpha=border_alpha)
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
        backdrop_weight: float = 1.0,
    ) -> None:
        super().__init__(parent)
        self.radius = radius
        self.base_alpha = alpha
        self.tint = tint
        self.tint_alpha = tint_alpha
        self.interactive = interactive
        self.backdrop_weight = backdrop_weight
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

    def backdrop_offset(self) -> QPointF:
        """本控件左上角的**屏幕全局坐标** —— 底图按整屏取，必须用全局坐标对齐。

        用全局坐标的好处：面板被拖到哪里，玻璃透出的就是那块桌面，
        不需要在移动时重新截屏（这也正是"实时"的关键）。
        """
        point = self.mapToGlobal(QPoint(0, 0))
        return QPointF(float(point.x()), float(point.y()))

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        # 悬停时整块玻璃亮一点 —— 触摸要有回应
        alpha = self.base_alpha + (0.12 if self.hovered else 0.0)
        paint_glass(
            painter,
            rect,
            self.radius,
            alpha=min(0.96, alpha),
            tint=self.tint,
            tint_alpha=self.tint_alpha + (0.06 if self.hovered else 0.0),
            offset=self.backdrop_offset(),
            backdrop_weight=self.backdrop_weight,
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
            # 刷新图标与设置、关闭保持一致，比之前 0.63R 略小一圈
            paint_refresh_icon(painter, c, r * 0.58, QColor(pen.color()))
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


class PillButton(QAbstractButton):
    """胶囊文字按钮：用于"添加设备 / 移除"这类次级操作。

    自绘而不用 QPushButton，是为了让圆角、半透明底和字体跟玻璃面板一致——
    走 QSS 的话在这些平台字体族上会掉回系统默认，视觉上会"跳出来"。
    """

    TONES: dict[str, tuple[str, tuple[int, int, int, int]]] = {
        "neutral": (theme.TEXT_SECONDARY, (120, 120, 128, 30)),
        "accent": (theme.ACCENT_BLUE, (10, 132, 255, 30)),
        "danger": (theme.DANGER, (255, 69, 58, 30)),
    }

    def __init__(
        self,
        text: str,
        parent: Optional[QWidget] = None,
        *,
        tone: str = "neutral",
        height: int = 24,
        min_width: int = 0,
    ) -> None:
        super().__init__(parent)
        self.tone = tone
        self._hover = False
        self.setText(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedHeight(height)
        metrics = QFontMetrics(theme.font(11.5, 500))
        self.setMinimumWidth(max(min_width, metrics.horizontalAdvance(text) + 24))
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

    def set_tone(self, tone: str) -> None:
        self.tone = tone
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        foreground, background = self.TONES.get(self.tone, self.TONES["neutral"])
        fill = QColor(*background)
        if self._hover:
            fill.setAlpha(min(255, background[3] + 26))
        if self.isDown():
            fill.setAlpha(min(255, background[3] + 44))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, radius, radius)
        painter.setPen(QColor(foreground))
        painter.setFont(theme.font(11.5, 500))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())
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
