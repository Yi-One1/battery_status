"""托盘：动态绘制电量图标 + 右键菜单。

图标完全由代码绘制（QPainter），跟随每类外设的自定义颜色变化，不需要图片资源。
两种模式：
  per-device —— 每个设备一个托盘图标，颜色区分类型，填充量即电量
  merged     —— 只留一个图标，显示最低电量设备的颜色与电量
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..core import autostart, presence
from ..core.config import DeviceRegistry, SettingsStore
from ..core.model import Snapshot
from . import theme

log = logging.getLogger(__name__)

ICON_SIZES = (16, 18, 20, 24, 32, 40, 48, 64)


def _bolt(center: QPointF, size: float) -> QPainterPath:
    path = QPainterPath()
    half = size / 2
    path.moveTo(center.x() + half * 0.2, center.y() - half)
    path.lineTo(center.x() - half * 0.55, center.y() + half * 0.1)
    path.lineTo(center.x() - half * 0.02, center.y() + half * 0.1)
    path.lineTo(center.x() - half * 0.25, center.y() + half)
    path.lineTo(center.x() + half * 0.6, center.y() - half * 0.16)
    path.lineTo(center.x() + half * 0.05, center.y() - half * 0.16)
    path.closeSubpath()
    return path


def _draw_icon(painter: QPainter, size: int, level: Optional[int], color: str, charging: bool, show_percent: bool, offline: bool) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # 电量告急时整枚图标转红，16px 下靠颜色比靠像素更容易被注意到
    critical = level is not None and level <= 10 and not charging
    base = QColor(theme.TEXT_TERTIARY if offline else (theme.DANGER if critical else color))
    ratio = 0.0 if level is None else max(0.0, min(100, level)) / 100.0

    scale = size / 16.0
    margin = 1.0 * scale
    body = QRectF(margin, 3.6 * scale, 11.2 * scale, 8.8 * scale)
    nub = QRectF(body.right() + 0.4 * scale, body.top() + 2.4 * scale, 1.5 * scale, 4.0 * scale)
    radius = 2.4 * scale

    if show_percent and level is not None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(base)
        painter.drawRoundedRect(QRectF(0.6 * scale, 0.6 * scale, 14.8 * scale, 14.8 * scale), 4.2 * scale, 4.2 * scale)
        painter.setPen(QColor(255, 255, 255))
        font = theme.font(9.4 * scale, 700)
        painter.setFont(font)
        painter.drawText(
            QRectF(0.6 * scale, 0.4 * scale, 14.8 * scale, 15.0 * scale),
            int(Qt.AlignmentFlag.AlignCenter),
            str(min(100, int(level))),
        )
        if charging:
            painter.setPen(QPen(QColor(255, 255, 255, 220), 1.1 * scale))
            painter.drawLine(QPointF(2.2 * scale, 13.0 * scale), QPointF(13.8 * scale, 13.0 * scale))
        return

    # 电池外壳
    painter.setPen(Qt.PenStyle.NoPen)
    dim = QColor(base)
    dim.setAlphaF(0.22)
    painter.setBrush(dim)
    painter.drawRoundedRect(nub, 0.6 * scale, 0.6 * scale)

    track = QColor(base)
    track.setAlphaF(0.28)
    painter.setBrush(track)
    painter.drawRoundedRect(body, radius, radius)

    if ratio > 0:
        inner_w = body.width() - 1.8 * scale
        inner_h = body.height() - 1.8 * scale
        # 低电量时保证至少一个圆点的可见度，否则 12% 会看起来像空槽
        fill_width = min(inner_w, max(inner_w * ratio, 2.2 * scale))
        painter.setBrush(base)
        painter.drawRoundedRect(
            QRectF(body.left() + 0.9 * scale, body.top() + 0.9 * scale, fill_width, inner_h),
            min(radius * 0.62, fill_width / 2),
            min(radius * 0.62, fill_width / 2),
        )

    painter.setBrush(Qt.BrushStyle.NoBrush)
    pen = QPen(QColor(base.red(), base.green(), base.blue(), 235), 1.15 * scale)
    painter.setPen(pen)
    painter.drawRoundedRect(body, radius, radius)

    if charging:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 235))
        painter.drawPath(_bolt(QPointF(body.center().x(), body.center().y()), 6.4 * scale))


def battery_icon(
    level: Optional[int],
    color: str,
    charging: bool = False,
    show_percent: bool = False,
    offline: bool = False,
) -> QIcon:
    icon = QIcon()
    for size in ICON_SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        _draw_icon(painter, size, level, color, charging, show_percent, offline)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class TrayController(QObject):
    show_panel = Signal()
    toggle_requested = Signal()
    show_settings = Signal()
    refresh_requested = Signal()
    quit_requested = Signal()

    def __init__(self, registry: DeviceRegistry, store: SettingsStore, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.registry = registry
        self.store = store
        self._icons: list[QSystemTrayIcon] = []
        self._snapshots: dict[str, Snapshot] = {}
        self._menu = QMenu()
        self._menu.aboutToShow.connect(self._populate_menu)
        self._menu.setStyleSheet(theme.app_stylesheet())

    # -- 图标管理 -----------------------------------------------------------
    def _ensure_icons(self, count: int) -> None:
        count = max(1, count)
        if len(self._icons) == count:
            return
        for icon in self._icons:
            icon.hide()
            icon.deleteLater()
        self._icons = []
        for _ in range(count):
            tray = QSystemTrayIcon(self._menu)
            tray.setContextMenu(self._menu)
            tray.activated.connect(self._on_activated)
            tray.show()
            self._icons.append(tray)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.toggle_requested.emit()

    def update_snapshots(self, snapshots: dict[str, Snapshot]) -> None:
        self._snapshots = snapshots
        prefs = self.store.prefs
        # 断连久了被收起的设备不再占托盘坑位，否则用久了会攒出一排灰电池
        devices = [
            d for d in presence.filter_visible(self.registry.enabled_devices(), snapshots, prefs) if d.id in snapshots
        ]
        self._visible = devices
        show_percent = prefs.tray_show_percent

        if prefs.tray_mode == "merged" or len(devices) <= 1:
            self._ensure_icons(1)
            snap, cfg = self._lowest(devices)
            if snap is None or cfg is None:
                self._icons[0].setIcon(battery_icon(None, theme.ACCENT_BLUE, offline=True))
                self._icons[0].setToolTip("外设电量 · 未检测到设备")
                return
            color = prefs.color_for_device(cfg)
            self._icons[0].setIcon(
                battery_icon(snap.level, color, bool(snap.charging), show_percent, not snap.online)
            )
            self._icons[0].setToolTip(self._tooltip())
            return

        self._ensure_icons(len(devices))
        lines = []
        for index, cfg in enumerate(devices):
            snap = snapshots[cfg.id]
            color = prefs.color_for_device(cfg)
            self._icons[index].setIcon(
                battery_icon(snap.level, color, bool(snap.charging), show_percent, not snap.online)
            )
            self._icons[index].setToolTip(self._device_line(cfg.display_name, snap))
            lines.append(self._device_line(cfg.display_name, snap))

    def _device_line(self, name: str, snap: Snapshot) -> str:
        level = "--" if snap.level is None else f"{snap.level}%"
        return f"{name}  {level}  {snap.status_text(self.store.prefs.low_battery_threshold)}"

    def _tooltip(self) -> str:
        lines = []
        for cfg in getattr(self, "_visible", self.registry.enabled_devices()):
            snap = self._snapshots.get(cfg.id)
            if snap:
                lines.append(self._device_line(cfg.display_name, snap))
        return "\n".join(lines) or "外设电量"

    def _lowest(self, devices: list) -> tuple[Optional[Snapshot], Optional[object]]:
        best_snap: Optional[Snapshot] = None
        best_cfg = None
        for cfg in devices:
            snap = self._snapshots.get(cfg.id)
            if snap is None or snap.level is None:
                continue
            if best_snap is None or best_snap.level is None or snap.level < best_snap.level:
                best_snap, best_cfg = snap, cfg
        if best_snap is None and devices:
            cfg = devices[0]
            return self._snapshots.get(cfg.id), cfg
        return best_snap, best_cfg

    # -- 菜单 ---------------------------------------------------------------
    def _populate_menu(self) -> None:
        self._menu.clear()
        prefs = self.store.prefs
        for cfg in getattr(self, "_visible", self.registry.enabled_devices()):
            snap = self._snapshots.get(cfg.id)
            if snap is None:
                continue
            action = QAction(self._device_line(cfg.display_name, snap), self._menu)
            action.setIcon(
                battery_icon(snap.level, prefs.color_for_device(cfg), bool(snap.charging), True, not snap.online)
            )
            action.triggered.connect(self.show_panel.emit)
            self._menu.addAction(action)
        self._menu.addSeparator()

        refresh = QAction("立即刷新", self._menu)
        refresh.triggered.connect(self.refresh_requested.emit)
        self._menu.addAction(refresh)

        settings_action = QAction("设置…", self._menu)
        settings_action.triggered.connect(self.show_settings.emit)
        self._menu.addAction(settings_action)

        autostart_action = QAction("开机自启动", self._menu)
        autostart_action.setCheckable(True)
        autostart_action.setChecked(autostart.is_enabled())
        autostart_action.toggled.connect(self._on_autostart)
        self._menu.addAction(autostart_action)

        self._menu.addSeparator()
        quit_action = QAction("退出", self._menu)
        quit_action.triggered.connect(self.quit_requested.emit)
        self._menu.addAction(quit_action)

    def _on_autostart(self, checked: bool) -> None:
        autostart.apply(checked)
        self.store.prefs.autostart = checked
        self.store.save()

    # -- 通知 ---------------------------------------------------------------
    def notify(self, title: str, message: str) -> None:
        if not self._icons:
            return
        self._icons[0].showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 6000)

    def shutdown(self) -> None:
        for icon in self._icons:
            icon.hide()
        self._icons = []
