"""已移除设备列表：可逐个恢复或彻底删除。"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.config import DeviceRegistry
from . import theme
from .glass import PillButton


def _label(text: str, size: float = 12.0, weight: int = 500, color: str = theme.TEXT_PRIMARY) -> QLabel:
    label = QLabel(text)
    label.setFont(theme.font(size, weight))
    label.setStyleSheet(f"color: {color}; background: transparent;")
    return label


class RemovedDevicesDialog(QDialog):
    """弹窗：列出已移除设备，每台可「恢复」或「彻底移除」。"""

    def __init__(self, registry: DeviceRegistry, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.FramelessWindowHint)
        self.registry = registry
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(360)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(0)

        shell = QWidget(self)
        shell.setStyleSheet(
            f"background: {theme.SURFACE}; border-radius: 20px; border: 1px solid {theme.BORDER_SOFT};"
        )
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(18, 18, 18, 18)
        shell_layout.setSpacing(12)

        header = _label("已移除设备", 14.0, 600)
        shell_layout.addWidget(header)

        self._list = QVBoxLayout()
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(6)
        self._list.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMaximumHeight(260)
        list_widget = QWidget()
        list_widget.setLayout(self._list)
        scroll.setWidget(list_widget)
        shell_layout.addWidget(scroll)

        close = PillButton("关闭", tone="neutral", height=28)
        close.clicked.connect(self.accept)
        shell_layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)

        outer.addWidget(shell)
        self._rebuild()

    def _rebuild(self) -> None:
        # 清理旧行（保留最后的 stretch）
        while self._list.count() > 1:
            item = self._list.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        removed = self.registry.removed_ids()
        if not removed:
            self._list.insertWidget(0, _label("没有已移除的设备", 11.0, 400, theme.TEXT_TERTIARY))
            return

        for device_id in removed:
            row = self._row(device_id)
            self._list.insertWidget(self._list.count() - 1, row)

    def _row(self, device_id: str) -> QWidget:
        cfg = next((d for d in self.registry.known_devices() if d.id == device_id), None)
        name = cfg.display_name if cfg else device_id

        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(8)

        label = _label(name, 12.0, 500)
        label.setToolTip(device_id)
        layout.addWidget(label, 1)

        restore = PillButton("恢复", tone="accent", height=22)
        restore.clicked.connect(lambda _=False, did=device_id: self._on_restore(did))
        layout.addWidget(restore)

        remove = PillButton("彻底移除", tone="danger", height=22)
        remove.clicked.connect(lambda _=False, did=device_id: self._on_permanent_remove(did))
        layout.addWidget(remove)

        return holder

    def _on_restore(self, device_id: str) -> None:
        self.registry.restore_device(device_id)
        self._rebuild()

    def _on_permanent_remove(self, device_id: str) -> None:
        self.registry.permanent_remove(device_id)
        self._rebuild()
