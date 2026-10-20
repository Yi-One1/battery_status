"""电量显示控件：环形 / 条形 / 数字三种样式，颜色跟随外设类型。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QConicalGradient, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import theme

STYLE_HEIGHT = {"ring": 84, "bar": 44, "number": 58}


def _bolt_path(center: QPointF, size: float) -> QPainterPath:
    path = QPainterPath()
    half = size / 2
    path.moveTo(center.x() + half * 0.18, center.y() - half)
    path.lineTo(center.x() - half * 0.55, center.y() + half * 0.12)
    path.lineTo(center.x() - half * 0.05, center.y() + half * 0.12)
    path.lineTo(center.x() - half * 0.22, center.y() + half)
    path.lineTo(center.x() + half * 0.58, center.y() - half * 0.14)
    path.lineTo(center.x() + half * 0.06, center.y() - half * 0.14)
    path.closeSubpath()
    return path


class BatteryGauge(QWidget):
    def __init__(
        self,
        style: str = "ring",
        color: str = theme.ACCENT_BLUE,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._style = style if style in STYLE_HEIGHT else "ring"
        self._color = color
        self._level: Optional[int] = None
        self._charging: Optional[bool] = None
        self._online = False
        self._alert = False
        height = STYLE_HEIGHT[self._style]
        self.setFixedHeight(height)
        if self._style == "ring":
            self.setFixedWidth(height)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # -- 状态 ---------------------------------------------------------------
    def set_state(self, level: Optional[int], charging: Optional[bool], online: bool, alert: bool = False) -> None:
        self._level = level
        self._charging = charging
        self._online = online
        self._alert = alert
        self.update()

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    @property
    def style(self) -> str:
        return self._style

    @property
    def color(self) -> str:
        return self._color

    def set_style(self, style: str) -> None:
        if style not in STYLE_HEIGHT or style == self._style:
            return
        self._style = style
        height = STYLE_HEIGHT[style]
        self.setFixedHeight(height)
        if style == "ring":
            self.setFixedWidth(height)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        else:
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        if self._style == "ring":
            return QSize(84, 84)
        return QSize(150, STYLE_HEIGHT[self._style])

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._style == "ring":
            self._paint_ring(painter)
        elif self._style == "bar":
            self._paint_bar(painter)
        else:
            self._paint_number(painter)
        painter.end()

    @property
    def _dim(self) -> bool:
        return not self._online or (self._level is None and not self._charging)

    @property
    def _charging_placeholder(self) -> bool:
        """电量被隐藏但正在充电（例如迈从充电占位符 50%）。"""
        return self._charging and self._level is None

    def _paint_ring(self, painter: QPainter) -> None:
        rect = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        thickness = 7.0
        arc_rect = rect.adjusted(thickness / 2, thickness / 2, -thickness / 2, -thickness / 2)
        ratio = 0.0 if self._level is None else max(0.0, min(1.0, self._level / 100.0))

        if self._charging_placeholder:
            # 充电占位符：整环绿色
            ratio = 1.0
            color = QColor(theme.OK)
        else:
            color = QColor(self._color)

        if self._alert and not self._charging and ratio > 0 and not self._charging_placeholder:
            glow = QColor(theme.DANGER)
            glow.setAlphaF(0.16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(rect.adjusted(2, 2, -2, -2))

        track = QColor(color if not self._dim else theme.TEXT_TERTIARY)
        track.setAlphaF(0.16 if not self._dim else 0.12)
        pen = QPen(track, thickness)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(arc_rect)

        if ratio > 0:
            gradient = QConicalGradient(arc_rect.center(), 90)
            base = color
            gradient.setColorAt(0.0, base)
            gradient.setColorAt(0.55, theme.lighten(color.name(), 0.42))
            gradient.setColorAt(1.0, base)
            pen = QPen(QBrush(gradient), thickness)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(arc_rect, 90 * 16, int(-360 * 16 * ratio))

        # 中心内容：电量被隐藏且充电时显示绿色闪电，否则显示数字
        if self._charging_placeholder:
            text = "\u26a1"
            font_num = theme.font(30, 600, theme.NUMBER_FAMILIES, -0.8)
        else:
            text = "--" if self._level is None else str(self._level)
            font_num = theme.font(25, 600, theme.NUMBER_FAMILIES, -0.8)
        font_pct = theme.font(11, 600)
        fm_num = QFontMetricsF(font_num)
        fm_pct = QFontMetricsF(font_pct)
        num_width = fm_num.horizontalAdvance(text)
        has_suffix = self._level is not None and not self._charging
        suffix_width = fm_pct.horizontalAdvance("%") + 1.5 if has_suffix else 0.0
        total = num_width + suffix_width
        left = rect.center().x() - total / 2
        baseline = rect.center().y() + (fm_num.ascent() - fm_num.descent()) / 2
        painter.setFont(font_num)
        painter.setPen(QColor(theme.TEXT_PRIMARY if not self._dim else theme.TEXT_TERTIARY))
        painter.drawText(QPointF(left, baseline), text)
        if has_suffix:
            painter.setFont(font_pct)
            painter.setPen(QColor(theme.TEXT_TERTIARY))
            painter.drawText(QPointF(left + num_width + 1.5, baseline), "%")

    def _paint_bar(self, painter: QPainter) -> None:
        rect = QRectF(self.rect())
        bar_height = 9.0
        bar = QRectF(rect.left(), rect.center().y() - bar_height / 2 + 5, rect.width(), bar_height)
        radius = bar_height / 2
        color = QColor(theme.OK if self._charging_placeholder else self._color)
        track = QColor(color if not self._dim else theme.TEXT_TERTIARY)
        track.setAlphaF(0.16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(bar, radius, radius)

        if self._charging_placeholder:
            fill = bar
        else:
            ratio = 0.0 if self._level is None else max(0.0, min(1.0, self._level / 100.0))
            fill = QRectF(bar.left(), bar.top(), max(bar_height, bar.width() * ratio), bar.height())
        if self._charging_placeholder or (self._level is not None and self._level > 0):
            gradient = QLinearGradient(fill.topLeft(), fill.topRight())
            gradient.setColorAt(0.0, color)
            gradient.setColorAt(1.0, theme.lighten(color.name(), 0.38))
            painter.setBrush(QBrush(gradient))
            painter.drawRoundedRect(fill, radius, radius)

        text = "\u26a1" if self._charging_placeholder else ("--" if self._level is None else str(self._level))
        painter.setFont(theme.font(17, 600, theme.NUMBER_FAMILIES, -0.4))
        painter.setPen(QColor(theme.TEXT_PRIMARY if not self._dim else theme.TEXT_TERTIARY))
        painter.drawText(
            QRectF(rect.left(), rect.top() - 4, rect.width(), 22),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            text + ("%" if self._level is not None and not self._charging_placeholder else ""),
        )

    def _paint_number(self, painter: QPainter) -> None:
        rect = QRectF(self.rect())
        color = QColor(theme.OK if self._charging_placeholder else self._color)
        if self._charging_placeholder:
            text = "\u26a1"
            font_num = theme.font(38, 600, theme.NUMBER_FAMILIES, -1.2)
        else:
            text = "--" if self._level is None else str(self._level)
            font_num = theme.font(34, 600, theme.NUMBER_FAMILIES, -1.2)
        painter.setFont(font_num)
        fm = QFontMetricsF(font_num)
        num_width = fm.horizontalAdvance(text)
        painter.setPen(QColor(theme.TEXT_PRIMARY if not self._dim else theme.TEXT_TERTIARY))
        painter.drawText(
            QRectF(rect.left(), rect.top() - 2, num_width + 4, rect.height() - 4),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            text,
        )
        if not self._charging_placeholder:
            painter.setFont(theme.font(12, 600))
            if self._charging:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(theme.OK))
                painter.drawPath(_bolt_path(QPointF(rect.left() + num_width + 14, rect.center().y()), 11))
            elif self._level is not None:
                painter.setPen(QColor(theme.TEXT_TERTIARY))
                painter.drawText(
                    QRectF(rect.left() + num_width + 2, rect.center().y() + 2, 20, 16),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    "%",
                )

        ratio = 1.0 if self._charging_placeholder else (0.0 if self._level is None else max(0.0, min(1.0, self._level / 100.0)))
        underline = QRectF(rect.left(), rect.bottom() - 6, rect.width(), 3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(60, 60, 67, 24))
        painter.drawRoundedRect(underline, 1.5, 1.5)
        if ratio > 0:
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(underline.left(), underline.top(), underline.width() * ratio, underline.height()), 1.5, 1.5)
