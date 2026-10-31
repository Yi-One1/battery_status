"""拖拽排序：设置页的竖直设备清单与主界面的双列卡片共用这一套。

分工很窄：只负责「按住手柄 → 跟踪落点 → 画落点标记 → 松手报新顺序」，
真正的重排与落盘由调用方处理（`reordered` 信号给出新的 key 序列）。

为什么用手柄而不是整行拖动：设置页每行里嵌着名称输入框、取色点、托盘开关、
样式分段控件，整行拖动会跟「在输入框里选字」「点开关」抢鼠标。
"""
from __future__ import annotations

from typing import Callable, Iterable, Optional

from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from . import theme

HANDLE_SIZE = (14, 24)


class DragHandle(QWidget):
    """⋮⋮ 手柄：只有按住它才进入拖动。"""

    def __init__(self, row: QWidget, controller: "ReorderController") -> None:
        super().__init__(row)
        self.setFixedSize(*HANDLE_SIZE)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("按住拖动可调整顺序")
        self._row = row
        self._controller = controller

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.TEXT_SECONDARY))
        painter.setOpacity(0.75)
        dot = 2.0
        step = 4.2
        cx = self.width() / 2
        cy = self.height() / 2 - step
        for row in range(3):
            for col in (-1, 1):
                painter.drawEllipse(
                    int(cx + col * step - dot / 2),
                    int(cy + row * step - dot / 2),
                    int(dot) + 1,
                    int(dot) + 1,
                )

    # -- 拖动 ---------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._controller.begin(self._row)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._controller.dragging:
            super().mouseMoveEvent(event)
            return
        self._controller.drag(event.globalPosition().toPoint())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._controller.dragging:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self._controller.drop()
            return
        super().mouseReleaseEvent(event)


class _Marker(QWidget):
    """落点标记：竖直清单画一条横线，网格画一个虚线框。"""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setVisible(False)
        self._grid_mode = False

    def show_at(self, rect: QRect, *, grid: bool) -> None:
        self._grid_mode = grid
        self.setGeometry(rect)
        self.raise_()
        self.setVisible(True)

    def hide_marker(self) -> None:
        self.setVisible(False)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        fill = QColor(theme.ACCENT_BLUE)
        fill.setAlpha(40 if self._grid_mode else 170)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        radius = 10 if self._grid_mode else min(2, self.height() / 2)
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), radius, radius)


class ReorderController(QObject):
    """管理一组行/卡片的拖拽顺序。

    `rows` 必须按显示顺序给出（调用方每次重建界面后同步一次）。
    `key_of` 把行 widget 映射成业务 id，`reordered` 发出的是 id 序列。
    """

    reordered = Signal(list)

    def __init__(
        self,
        host: QWidget,
        *,
        key_of: Callable[[QWidget], str],
        columns: int = 1,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent or host)
        self.host = host
        self.columns = max(1, int(columns))
        self.key_of = key_of
        self.rows: list[QWidget] = []
        self._dragging: Optional[QWidget] = None
        self._target = -1
        self._marker = _Marker(host)

    # -- 状态 ---------------------------------------------------------------
    @property
    def dragging(self) -> bool:
        return self._dragging is not None

    def set_rows(self, rows: Iterable[QWidget]) -> None:
        self.rows = [row for row in rows]
        self._dragging = None
        self._target = -1
        self._marker.hide_marker()

    # -- 拖动 ---------------------------------------------------------------
    def begin(self, row: QWidget) -> None:
        if row not in self.rows:
            return
        self._dragging = row
        self._target = self.rows.index(row)

    def drag(self, global_pos: QPoint) -> None:
        if self._dragging is None:
            return
        self._target = self._index_at(self.host.mapFromGlobal(global_pos))
        rect = self._marker_rect(self._target)
        if rect is not None:
            self._marker.show_at(rect, grid=self.columns > 1)

    def drop(self) -> None:
        if self._dragging is None:
            return
        source = self.rows.index(self._dragging)
        target = self._target
        self._dragging = None
        self._target = -1
        self._marker.hide_marker()
        if target < 0 or target in (source, source + 1):
            return  # 落在自己原来的位置（前后都算没变）
        order = list(self.rows)
        moved = order.pop(source)
        order.insert(target - 1 if target > source else target, moved)
        self.rows = order
        self.reordered.emit([self.key_of(row) for row in order])

    # -- 几何 ---------------------------------------------------------------
    def _index_at(self, pos: QPoint) -> int:
        rows = [r for r in self.rows if r.isVisible()]
        if not rows:
            return 0
        if self.columns == 1:
            for index, row in enumerate(self.rows):
                if pos.y() < row.geometry().center().y():
                    return index
            return len(self.rows)
        # 网格：取中心最近的一格，再按左右半边决定插在它前面还是后面
        best_index, best_distance = 0, None
        for index, row in enumerate(self.rows):
            geo = row.geometry()
            dx = pos.x() - geo.center().x()
            dy = pos.y() - geo.center().y()
            distance = dx * dx + dy * dy
            if best_distance is None or distance < best_distance:
                best_index, best_distance = index, distance
        geo = self.rows[best_index].geometry()
        if pos.x() > geo.center().x():
            return min(len(self.rows), best_index + 1)
        return best_index

    def _marker_rect(self, target: int) -> Optional[QRect]:
        if not self.rows:
            return None
        if self.columns == 1:
            if target < len(self.rows):
                geo = self.rows[target].geometry()
                y = geo.top() - 2
            else:
                geo = self.rows[-1].geometry()
                y = geo.bottom() - 1
            return QRect(geo.left(), y, geo.width(), 3)
        if target < len(self.rows):
            return self.rows[target].geometry().adjusted(1, 1, -1, -1)
        return self.rows[-1].geometry().adjusted(1, 1, -1, -1)
