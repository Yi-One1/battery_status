"""托盘：动态绘制电量图标 + 右键菜单。

图标完全由代码绘制（QPainter），跟随每类外设的自定义颜色变化，不需要图片资源。
每台可见设备固定一枚独立图标，顺序与主界面一致（行优先）；
可在设置页按设备隐藏托盘显示。全部隐藏/无设备时回落一枚灰色占位图标。
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..core import autostart, presence
from ..core.config import DeviceRegistry, SettingsStore
from ..core.model import Snapshot
from . import theme

log = logging.getLogger(__name__)

ICON_SIZES = (16, 18, 20, 24, 32, 40, 48, 64)
# 整组换血间隔：旧图标注销与新图标注册之间留的间隙（毫秒），
# 让资源管理器彻底释放旧槽位；测试里会被改成 0。
SWAP_DELAY_MS = 250


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

    if show_percent:
        if level is None and charging:
            # 充电占位符（电量不可读）：整块绿色 + 白色闪电，与主界面"充电中"一致
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.OK))
            painter.drawRoundedRect(QRectF(0.6 * scale, 0.6 * scale, 14.8 * scale, 14.8 * scale), 4.2 * scale, 4.2 * scale)
            painter.setBrush(QColor(255, 255, 255))
            painter.drawPath(_bolt(QPointF(8.0 * scale, 8.2 * scale), 8.6 * scale))
            return
        if level is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(base)
            painter.drawRoundedRect(QRectF(0.6 * scale, 0.6 * scale, 14.8 * scale, 14.8 * scale), 4.2 * scale, 4.2 * scale)
            painter.setPen(QColor(255, 255, 255))
            # 只画数值，不带 % 符号；充电时数值上移，底部让位给绿色闪电
            text_rect = QRectF(
                0.6 * scale,
                (0.2 if charging else 0.4) * scale,
                14.8 * scale,
                (11.2 if charging else 14.6) * scale,
            )
            painter.setFont(theme.font(9.4 * scale, 700))
            painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignCenter), str(min(100, int(level))))
            if charging:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(theme.OK))
                painter.drawPath(_bolt(QPointF(8.0 * scale, 13.0 * scale), 4.8 * scale))
            return
        # 电量未知且未充电：回落到电池外壳画法

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
        # 图标对应的设备 id 序列。Windows 按创建（注册）顺序分配托盘槽位，
        # 只换图不重建的话，槽位顺序不会跟随设备重排 —— 所以序列变化必须重建。
        self._icon_ids: list[str] = []
        # 整组换血：注销旧图标与新图标注册分两轮（间隔 SWAP_DELAY_MS），
        # 给资源管理器留出释放旧槽位的时间，否则同轮注销+注册的槽位顺序会错乱。
        self._pending_ids: Optional[list[str]] = None
        self._swap_old_icons: list[QSystemTrayIcon] = []
        self._queued_renders: Optional[list[tuple[object, str]]] = None
        self._swap_timer = QTimer(self)
        self._swap_timer.setSingleShot(True)
        self._swap_timer.setInterval(SWAP_DELAY_MS)
        self._swap_timer.timeout.connect(self._do_swap)
        self._snapshots: dict[str, Snapshot] = {}
        self._menu = QMenu()
        self._menu.aboutToShow.connect(self._populate_menu)
        self._menu.setStyleSheet(theme.app_stylesheet())

    # -- 图标管理 -----------------------------------------------------------
    def _ensure_icons(self, count: int, ids: Optional[list[str]] = None) -> None:
        count = max(1, count)
        wanted_ids = [str(x) for x in (ids or [])]
        if self._pending_ids is not None:
            if self._pending_ids == wanted_ids:
                return  # 已安排同目标重建，等换血定时器执行
        elif len(self._icons) == count and self._icon_ids == wanted_ids:
            return

        if ids is None:
            # 占位图标：单枚、无顺序可言，立即注销全部并直接创建
            self._pending_ids = None
            self._queued_renders = None
            self._teardown(self._icons)
            self._teardown(self._swap_old_icons)
            self._swap_old_icons = []
            self._swap_timer.stop()
            tray = QSystemTrayIcon(self._menu)
            tray.setContextMenu(self._menu)
            tray.activated.connect(self._on_activated)
            tray.show()
            self._icons = [tray]
            self._icon_ids = []
            return

        if self._pending_ids is None:
            # 重建开始：旧图标先留着显示（不闪烁），到点整组注销、按新序重建
            self._swap_old_icons = list(self._icons)
            self._icons = []
            self._icon_ids = []
        # 若重建途中目标序列又变了：只更新目标，待注销列表保持原样
        self._pending_ids = wanted_ids
        self._swap_timer.start()

    def _do_swap(self) -> None:
        if self._pending_ids is None:
            return
        self._teardown(self._swap_old_icons)
        self._swap_old_icons = []
        ids = self._pending_ids
        self._pending_ids = None
        self._create_icons(ids)

    def _teardown(self, icons: list) -> None:
        for icon in icons:
            icon.hide()
            icon.deleteLater()

    def _create_icons(self, ids: list[str]) -> None:
        icons: list[QSystemTrayIcon] = []
        for _ in ids:
            tray = QSystemTrayIcon(self._menu)
            tray.setContextMenu(self._menu)
            tray.activated.connect(self._on_activated)
            tray.show()
            icons.append(tray)
        self._icons = icons
        self._icon_ids = list(ids)
        queued = self._queued_renders
        self._queued_renders = None
        if queued and len(queued) == len(icons):
            self._apply_renders(queued)

    def _apply_renders(self, renders: list[tuple[object, str]]) -> None:
        """把 (图标, 提示) 应用到当前图标组；换血期间先入队，换血完成后补上。"""
        if self._pending_ids is not None:
            self._queued_renders = renders
            return
        for icon, (qicon, tooltip) in zip(self._icons, renders):
            icon.setIcon(qicon)
            icon.setToolTip(tooltip)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # 单击固定为「切换主面板」，且面板打开时总落在电量显示页（Panel.show_panel 负责）；
        # 双击功能已删除：Windows 双击前会先发一次 Trigger，这里忽略 DoubleClick 即可。
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_requested.emit()

    def update_snapshots(self, snapshots: dict[str, Snapshot]) -> None:
        self._snapshots = snapshots
        prefs = self.store.prefs
        # 断连久了被收起的设备不再占托盘坑位，否则用久了会攒出一排灰电池；
        # 被用户隐藏的设备同样不占坑（仅托盘层面，主界面不受影响）；
        # 顺序必须与主界面一致：同一套 ordered_devices 排序
        visible = [d for d in presence.filter_visible(self.registry.enabled_devices(), snapshots, prefs) if d.id in snapshots]
        visible = presence.tray_visible_devices(visible, prefs)
        devices = presence.ordered_devices(visible, prefs)
        self._visible = devices
        show_percent = prefs.tray_show_percent

        if not devices:
            # 一台可见设备都没有：保留一枚灰色占位图标，应用仍能从托盘打开
            self._ensure_icons(1)
            self._apply_renders([(battery_icon(None, theme.ACCENT_BLUE, offline=True), "外设电量 · 未检测到设备")])
            return

        renders: list[tuple[object, str]] = []
        for cfg in devices:
            snap = snapshots[cfg.id]
            renders.append((
                battery_icon(
                    snap.display_level,
                    prefs.color_for_device(cfg),
                    bool(snap.display_charging),
                    show_percent,
                    not snap.online,
                ),
                self._device_line(cfg.display_name, snap),
            ))
        # 传入 id 序列：顺序变化时整组换血，让 Windows 槽位跟着主界面重排
        self._ensure_icons(len(devices), [d.id for d in devices])
        self._apply_renders(renders)

    def _device_line(self, name: str, snap: Snapshot) -> str:
        level = "--" if snap.display_level is None else f"{snap.display_level}%"
        return f"{name}  {level}  {snap.status_text(self.store.prefs.low_battery_threshold)}"

    def _tooltip(self) -> str:
        lines = []
        for cfg in getattr(self, "_visible", self.registry.enabled_devices()):
            snap = self._snapshots.get(cfg.id)
            if snap:
                lines.append(self._device_line(cfg.display_name, snap))
        return "\n".join(lines) or "外设电量"

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
                battery_icon(
                    snap.display_level,
                    prefs.color_for_device(cfg),
                    bool(snap.display_charging),
                    True,
                    not snap.online,
                )
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
        self._swap_timer.stop()
        self._pending_ids = None
        self._queued_renders = None
        self._teardown(self._icons)
        self._teardown(self._swap_old_icons)
        self._icons = []
        self._swap_old_icons = []
        self._icon_ids = []
