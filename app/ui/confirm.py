"""二次确认对话框：用于"移除设备"这类不可一眼撤销的操作。

设计上有意做得**小、静、只有一个决定**：
标题一句话说清要做什么，正文给后果，底部两个胶囊按钮（取消 / 危险操作）。
不用 QMessageBox —— 它在 Windows 上是系统原生样式，会从柔光玻璃里"跳出来"。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from . import theme
from .glass import Divider, IconButton, PillButton, paint_glass

DIALOG_WIDTH = 344


def _text(value: str, size: float, weight: int, color: str, wrap: bool = True) -> QLabel:
    label = QLabel(value)
    label.setFont(theme.font(size, weight))
    label.setStyleSheet(f"color: {color}; background: transparent;")
    label.setWordWrap(wrap)
    return label


class ConfirmDialog(QDialog):
    """一个标题、一段后果说明、一个确认按钮。"""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        title: str = "确认",
        message: str = "",
        detail: str = "",
        confirm_text: str = "确定",
        cancel_text: str = "取消",
        tone: str = "danger",
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setWindowTitle(title)
        self.setFixedWidth(DIALOG_WIDTH)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 16)
        outer.setSpacing(0)
        outer.addWidget(self._build_header(title))
        outer.addWidget(self._body(message, detail))
        outer.addWidget(Divider())
        outer.addWidget(self._build_footer(confirm_text, cancel_text, tone))

    # -- 构建 ---------------------------------------------------------------
    def _build_header(self, title: str) -> QWidget:
        header = QWidget()
        header.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(2, 0, 0, 6)
        layout.setSpacing(6)
        layout.addWidget(_text(title, 14.5, 600, theme.TEXT_PRIMARY, wrap=False), 1)
        close = IconButton("close", header, diameter=26)
        close.setToolTip("取消")
        close.clicked.connect(self.reject)
        layout.addWidget(close)
        return header

    def _body(self, message: str, detail: str) -> QWidget:
        box = QWidget()
        box.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(2, 0, 2, 10)
        layout.setSpacing(6)
        layout.addWidget(_text(message, 12.0, 400, theme.TEXT_PRIMARY))
        if detail:
            layout.addWidget(_text(detail, 10.5, 400, theme.TEXT_TERTIARY))
        return box

    def _build_footer(self, confirm_text: str, cancel_text: str, tone: str) -> QWidget:
        box = QWidget()
        box.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)
        layout.addStretch(1)

        cancel = PillButton(cancel_text, tone="neutral", height=28)
        cancel.setMinimumWidth(72)
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)

        confirm = PillButton(confirm_text, tone=tone, height=28)
        confirm.setMinimumWidth(72)
        confirm.clicked.connect(self.accept)
        layout.addWidget(confirm)
        self.confirm_button = confirm
        return box

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # QAbstractButton 没有"默认按钮"的概念，回车得自己接
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.accept()
            return
        super().keyPressEvent(event)

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 独立窗口，面板那份背景板位置对不上，保持一块更实的柔光玻璃
        paint_glass(
            painter,
            QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
            22,
            alpha=0.94,
            border_alpha=0.92,
            backdrop_weight=0.0,
        )
        painter.end()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._center_on_parent()

    def _center_on_parent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        anchor = parent.window().frameGeometry()
        self.adjustSize()
        self.move(
            anchor.center().x() - self.width() // 2,
            anchor.center().y() - self.height() // 2,
        )

    # -- 便捷入口 -----------------------------------------------------------
    @classmethod
    def ask(cls, parent: Optional[QWidget], **kwargs) -> bool:
        dialog = cls(parent, **kwargs)
        return dialog.exec() == QDialog.DialogCode.Accepted
