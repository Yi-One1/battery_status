"""新增设备对话框：列出检测到的设备，或按 VID/PID 手动登记。

两个入口对应两种现实场景：
  * **检测到的设备** —— 设备正插着，但配置里没有它（换了新外设）。
    点一下即可入册，之后可在设置里继续调颜色/样式。
  * **手动填写 VID/PID** —— 设备此刻不在，或者名字太乱找不到。
    填完就写进用户配置，等它插上就能读。
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.config import DeviceConfig, DeviceRegistry
from ..core.discovery import (
    DiscoveredDevice,
    available_devices,
    build_device_config,
    build_manual_config,
    make_device_id,
)
from ..core.hidapi_io import HidDeviceInfo, enumerate_devices
from . import theme
from .glass import Divider, IconButton, PillButton, SegmentedControl, paint_glass

log = logging.getLogger(__name__)

DIALOG_WIDTH = 372
LIST_HEIGHT = 196


def _text(value: str, size: float = 12.0, weight: int = 500, color: str = theme.TEXT_PRIMARY) -> QLabel:
    label = QLabel(value)
    label.setFont(theme.font(size, weight))
    label.setStyleSheet(f"color: {color}; background: transparent;")
    return label


def _section(title: str) -> QWidget:
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 4, 0, 2)
    layout.setSpacing(0)
    layout.addWidget(_text(title, 10.5, 600, theme.TEXT_TERTIARY))
    return holder


class _CandidateRow(QWidget):
    """一台检测到的设备：左边型号 + 键值，右边一个「添加」。"""

    def __init__(self, device: DiscoveredDevice, on_add, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.device = device
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 8, 6)
        layout.setSpacing(8)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(1)
        text_box.addWidget(_text(device.display_name, 12.0, 500))
        pages = "、".join(f"0x{page:04X}" for page in device.usage_pages[:3]) or "未提供"
        hint = f"{device.key} · {device.interfaces} 个接口 · {pages}"
        if device.vendor_specific:
            hint += " · 含厂商通道"
        hint_label = _text(hint, 9.5, 400, theme.TEXT_TERTIARY)
        # 长串不该撑宽窗口：固定宽对话里让标签让位，超出部分裁掉
        hint_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text_box.addWidget(hint_label)
        layout.addLayout(text_box, 1)

        button = PillButton("添加", tone="accent")
        button.clicked.connect(lambda _=False, d=device: on_add(d))
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("#FFFFFF", 0.55))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        painter.end()


class AddDeviceDialog(QDialog):
    device_added = Signal(object)  # DeviceConfig

    def __init__(
        self,
        registry: DeviceRegistry,
        parent: Optional[QWidget] = None,
        *,
        infos: Optional[list[HidDeviceInfo]] = None,
    ) -> None:
        super().__init__(parent)
        self.registry = registry
        self._infos = infos
        self._drag_offset: Optional[QPoint] = None
        self.created: list[DeviceConfig] = []

        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setWindowTitle("添加设备")
        self.setFixedWidth(DIALOG_WIDTH)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 16)
        outer.setSpacing(0)

        outer.addWidget(self._build_header())
        outer.addWidget(_section("检测到的设备"))
        outer.addWidget(self._build_candidate_list())
        outer.addWidget(Divider())
        outer.addWidget(_section("按 VID / PID 手动添加"))
        outer.addWidget(self._build_manual_form())
        outer.addWidget(Divider())
        outer.addWidget(self._build_footer())

    # -- 构建 ---------------------------------------------------------------
    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setStyleSheet("background: transparent;")
        header.installEventFilter(self)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(2, 0, 0, 8)
        layout.setSpacing(6)
        layout.addWidget(_text("添加设备", 15.0, 600), 1)
        close = IconButton("close", header, diameter=26)
        close.setToolTip("关闭")
        close.clicked.connect(self.reject)
        layout.addWidget(close)
        return header

    def _build_candidate_list(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setStyleSheet("background: transparent;")
        scroll.setFixedHeight(LIST_HEIGHT)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(content)
        self._list_layout.setContentsMargins(0, 0, 4, 0)
        self._list_layout.setSpacing(6)
        scroll.setWidget(content)
        self._scroll = scroll
        self._fill_candidates()
        return scroll

    def _fill_candidates(self) -> None:
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        devices = self._candidates()
        if not devices:
            empty = _text("没有发现未登记的设备。插好接收器后重新打开本窗口即可。", 11.0, 400, theme.TEXT_TERTIARY)
            empty.setWordWrap(True)
            self._list_layout.addWidget(empty)
            self._list_layout.addStretch(1)
            return
        for device in devices:
            self._list_layout.addWidget(_CandidateRow(device, self._add_discovered))
        self._list_layout.addStretch(1)

    def _candidates(self) -> list[DiscoveredDevice]:
        infos = self._infos if self._infos is not None else enumerate_devices()
        try:
            return available_devices(infos, self.registry, include_generic=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("枚举设备失败：%s", exc)
            return []

    def _build_manual_form(self) -> QWidget:
        box = QWidget()
        box.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(6)

        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.setSpacing(6)
        self.vid_edit = self._line_edit("VID（十六进制，如 258A）", 96)
        self.pid_edit = self._line_edit("PID（如 01BB）", 96)
        self.name_edit = self._line_edit("显示名称，可留空")
        key_row.addWidget(self.vid_edit)
        key_row.addWidget(self.pid_edit)
        key_row.addWidget(self.name_edit, 1)
        layout.addLayout(key_row)

        self.type_picker = SegmentedControl(self.registry.type_options() or [("mouse", "鼠标")], height=24)
        layout.addWidget(self.type_picker)

        self.hint = _text("", 10.0, 400, theme.DANGER)
        self.hint.setVisible(False)
        layout.addWidget(self.hint)
        return box

    @staticmethod
    def _line_edit(placeholder: str, width: int = 0) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setFont(theme.font(11.5, 400))
        edit.setStyleSheet(
            "QLineEdit { background: rgba(120,120,128,0.10); border: none; border-radius: 7px;"
            f" padding: 5px 8px; color: {theme.TEXT_PRIMARY}; }}"
            f"QLineEdit::placeholder {{ color: {theme.TEXT_TERTIARY}; }}"
        )
        if width:
            edit.setFixedWidth(width)
        edit.setFixedHeight(26)
        return edit

    def _build_footer(self) -> QWidget:
        box = QWidget()
        box.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(box)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        hint = _text("添加后可在设置里继续调整颜色与样式", 10.0, 400, theme.TEXT_TERTIARY)
        hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(hint, 1)
        add = PillButton("手动添加", tone="neutral", height=26)
        add.setMinimumWidth(84)
        add.clicked.connect(self._add_manual)
        layout.addWidget(add, 0)
        return box

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        paint_glass(painter, rect, 20, alpha=0.94, border_alpha=0.9)
        painter.end()

    # -- 拖拽（无边框窗口）--------------------------------------------------
    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            return False
        if event.type() == QEvent.Type.MouseMove and self._drag_offset is not None:
            if event.buttons() & Qt.MouseButton.LeftButton:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                return False
        if event.type() == QEvent.Type.MouseButtonRelease:
            self._drag_offset = None
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    # -- 动作 ---------------------------------------------------------------
    def _register(self, cfg: DeviceConfig) -> None:
        self.registry.add_device(cfg)
        self.created.append(cfg)
        self.device_added.emit(cfg)
        self.accept()

    def _add_discovered(self, device: DiscoveredDevice) -> None:
        device_id = make_device_id(device, self.registry)
        self._register(build_device_config(device, device_id))

    def _add_manual(self) -> None:
        try:
            vid = int(self.vid_edit.text().strip(), 16)
            pid = int(self.pid_edit.text().strip(), 16)
        except ValueError:
            self._show_hint("VID / PID 需要填十六进制数字，例如 258A / 01BB")
            return
        if not (0 < vid <= 0xFFFF and 0 < pid <= 0xFFFF):
            self._show_hint("VID / PID 超出 1~FFFF 的范围")
            return
        name = self.name_edit.text().strip()
        device = DiscoveredDevice(
            vid=vid,
            pid=pid,
            name=name or f"{vid:04X}:{pid:04X}",
            manufacturer="",
            usage_pages=(),
            interfaces=0,
            vendor_specific=False,
        )
        device_id = make_device_id(device, self.registry)
        cfg = build_manual_config(
            vid,
            pid,
            name,
            self.type_picker.value(),
            device_id,
        )
        self._register(cfg)

    def _show_hint(self, message: str) -> None:
        self.hint.setText(message)
        self.hint.setVisible(True)
