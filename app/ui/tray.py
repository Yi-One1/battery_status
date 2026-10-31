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
from ..core.paths import APP_DISPLAY_NAME
from . import theme

log = logging.getLogger(__name__)

ICON_SIZES = (16, 18, 20, 24, 32, 40, 48, 64)
# 整组换血间隔：旧图标注销与新图标注册之间留的间隙（毫秒），
# 让资源管理器彻底释放旧槽位；测试里会被改成 0。
SWAP_DELAY_MS = 250
# 逐个注册间隔：Windows 托盘按「注册先后」排槽位，一次性批量注册时到达顺序
# 并不保证，设备多了就会出现托盘顺序与主界面不一致、重启后又变的情况。
# 每枚之间留一点间隙，让 shell 按我们给的顺序落位；测试里会被改成 0。
CREATE_INTERVAL_MS = 70

# 图标上的前景色（数字 / 闪电）在深底与浅底各用一套。
# 设备颜色是用户自选的，浅到接近白色的配色上画白字等于看不见，
# 所以按底色的相对亮度自动选，而不是写死白色。
FG_LIGHT = QColor(255, 255, 255)
FG_DARK = QColor(theme.TEXT_PRIMARY)

# 图标几何：数字模式是一整块圆角徽标，尽量占满 16px 画布（留 0.5 抗锯齿余量）。
# 16px 是任务栏最常见的实际尺寸，字号/留白都是按它调的：
# 旧版数字只有 9.4px 且四周留白大，缩到任务栏里就糊成一团。
BADGE_INSET = 0.5
BADGE_RADIUS = 4.6
PERCENT_FONT = 11.6          # 两位数
PERCENT_FONT_COMPACT = 9.0   # 三位数（100）收字号，否则会顶到徽标边缘
CHARGE_DOT_RADIUS = 2.4


def relative_luminance(color: QColor) -> float:
    """WCAG 相对亮度（0 = 全黑，1 = 全白）。"""

    def channel(value: int) -> float:
        srgb = value / 255.0
        return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(color.red()) + 0.7152 * channel(color.green()) + 0.0722 * channel(color.blue())


def foreground_on(background: QColor) -> QColor:
    """按底色亮度选前景：浅底用深字，深底用白字（阈值 0.5，业界通行口径）。"""
    return FG_DARK if relative_luminance(background) > 0.5 else FG_LIGHT


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


def _badge_rect(scale: float) -> QRectF:
    """数字模式的圆角徽标：几乎占满整个画布。"""
    side = 16.0 - 2 * BADGE_INSET
    return QRectF(BADGE_INSET * scale, BADGE_INSET * scale, side * scale, side * scale)


def _draw_charge_dot(painter: QPainter, scale: float) -> None:
    """充电标记：右下角的小绿点。

    旧做法是把数字上移、在底部画闪电，16px 下两者互相挤，数字被迫缩小。
    改成角标后数字保持完整字号、位置不变。
    """
    radius = CHARGE_DOT_RADIUS * scale
    center = (16.0 - BADGE_INSET) * scale - radius
    green = QColor(theme.OK)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(green)
    painter.drawEllipse(QPointF(center, center), radius, radius)
    painter.setBrush(foreground_on(green))
    painter.drawPath(_bolt(QPointF(center, center), radius * 1.9))


def _draw_icon(painter: QPainter, size: int, level: Optional[int], color: str, charging: bool, show_percent: bool, offline: bool) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # 电量告急时整枚图标转红，16px 下靠颜色比靠像素更容易被注意到
    critical = level is not None and level <= 10 and not charging
    base = QColor(theme.TEXT_TERTIARY if offline else (theme.DANGER if critical else color))
    ratio = 0.0 if level is None else max(0.0, min(100, level)) / 100.0

    scale = size / 16.0
    badge = _badge_rect(scale)
    badge_radius = BADGE_RADIUS * scale

    if show_percent and level is not None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(base)
        painter.drawRoundedRect(badge, badge_radius, badge_radius)
        # 数字跟着底色走：浅色设备色配深字，深色配白字；不带 % 符号
        painter.setPen(foreground_on(base))
        text = str(min(100, int(level)))
        font_size = PERCENT_FONT if len(text) < 3 else PERCENT_FONT_COMPACT
        painter.setFont(theme.font(font_size * scale, 700))
        painter.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), text)
        if charging:
            _draw_charge_dot(painter, scale)
        return

    if show_percent and charging:
        # 充电占位符（电量不可读）：整块绿色 + 闪电，与主界面"充电中"一致
        solid = QColor(theme.OK)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(solid)
        painter.drawRoundedRect(badge, badge_radius, badge_radius)
        painter.setBrush(foreground_on(solid))
        painter.drawPath(_bolt(QPointF(8.0 * scale, 8.2 * scale), 9.4 * scale))
        return

    # 电量未知且未充电 / 未开启数字模式：回落到电池外壳画法
    margin = 0.9 * scale
    body = QRectF(margin, 3.5 * scale, 11.4 * scale, 9.0 * scale)
    nub = QRectF(body.right() + 0.4 * scale, body.top() + 2.4 * scale, 1.6 * scale, 4.2 * scale)
    radius = 2.6 * scale

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
        bolt = QColor(foreground_on(base))
        bolt.setAlpha(235)
        painter.setBrush(bolt)
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
        # 目标序列：要建成什么样。与 _icon_ids 相同且非重建中即为稳定态。
        self._target_ids: list[str] = []
        # 整组换血：注销旧图标与新图标注册分两轮（间隔 SWAP_DELAY_MS），
        # 给资源管理器留出释放旧槽位的时间，否则同轮注销+注册的槽位顺序会错乱。
        self._swap_old_icons: list[QSystemTrayIcon] = []
        self._swap_pending = False
        self._swap_timer = QTimer(self)
        self._swap_timer.setSingleShot(True)
        self._swap_timer.setInterval(SWAP_DELAY_MS)
        self._swap_timer.timeout.connect(self._do_swap)
        # 逐个注册：见 CREATE_INTERVAL_MS 的说明
        self._create_queue: list[str] = []
        self._created: dict[str, QSystemTrayIcon] = {}
        self._create_timer = QTimer(self)
        self._create_timer.setSingleShot(True)
        self._create_timer.timeout.connect(self._create_next)
        # 设备 id → (图标, 提示)。按 id 索引而不是按下标，重建中途也能对上号
        self._renders: dict[str, tuple[object, str]] = {}
        self._snapshots: dict[str, Snapshot] = {}
        self._menu = QMenu()
        self._menu.aboutToShow.connect(self._populate_menu)
        self._menu.setStyleSheet(theme.app_stylesheet())

    # -- 图标管理 -----------------------------------------------------------
    def _ensure_icons(self, ids: Optional[list[str]] = None) -> None:
        wanted = [str(x) for x in (ids or [])]

        if ids is None:
            # 占位图标：单枚、无顺序可言，立即注销全部并直接创建
            self._swap_pending = False
            self._create_queue = []
            self._create_timer.stop()
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
            self._target_ids = []
            self._created = {}
            return

        if self._target_ids == wanted and (self._swap_pending or self._create_queue):
            return  # 已经朝着同一目标在重建/逐个注册，等定时器走完
        if self._target_ids == wanted and self._icon_ids == wanted:
            return  # 稳定态：只换图，不重建

        self._target_ids = wanted
        if self._swap_pending:
            return  # 换血已排队，目标序列已在上面更新，到点按新目标建
        # 重建开始：旧图标先留着显示（不闪烁），到点整组注销、按新序重建
        self._swap_old_icons = list(self._icons)
        self._icons = []
        self._icon_ids = []
        self._swap_pending = True
        self._swap_timer.start()

    def _do_swap(self) -> None:
        if not self._swap_pending:
            return
        self._teardown(self._swap_old_icons)
        self._teardown(self._icons)
        self._icons = []
        self._icon_ids = []
        self._swap_old_icons = []
        self._swap_pending = False
        # 逆向注册：Windows 把新注册的图标插在托盘区最前面，正向注册会得到反序，
        # 所以这里按显示顺序倒着建，最终落位才是 1,2,3,4。
        self._created = {}
        self._create_queue = list(reversed(self._target_ids))
        self._create_next()

    def _teardown(self, icons: list) -> None:
        for icon in icons:
            icon.hide()
            icon.deleteLater()

    def _create_next(self) -> None:
        """逐枚注册（注册顺序见 _do_swap），避免批量注册时 shell 收到顺序不保证。"""
        if not self._create_queue:
            self._create_timer.stop()
            return
        device_id = self._create_queue.pop(0)
        tray = QSystemTrayIcon(self._menu)
        tray.setContextMenu(self._menu)
        tray.activated.connect(self._on_activated)
        tray.show()
        self._created[device_id] = tray
        # 内部列表始终按显示顺序排（创建先后不影响它），已建的为空缺留位
        self._icon_ids = [d for d in self._target_ids if d in self._created]
        self._icons = [self._created[d] for d in self._icon_ids]
        self._apply_renders()
        if self._create_queue:
            self._create_timer.start(max(0, CREATE_INTERVAL_MS))

    def _apply_renders(self) -> None:
        """把已就绪的图标/提示贴到对应设备的槽位上（按 id 匹配）。"""
        for icon, device_id in zip(self._icons, self._icon_ids):
            render = self._renders.get(device_id)
            if render is None:
                continue
            qicon, tooltip = render
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
        # 顺序必须与主界面一致：同一套 ordered_devices 排序。
        # 刻意「不」按「快照里有没有这台设备」过滤：启动时快照要等首轮扫描才到，
        # 若此时少建几枚、扫描后再补，Windows 会把新图标排到旧图标后面 ——
        # 这正是托盘顺序和主界面不一致、每次重启还都不一样的原因。
        visible = presence.filter_visible(self.registry.enabled_devices(), snapshots, prefs)
        visible = presence.tray_visible_devices(visible, prefs)
        devices = presence.ordered_devices(visible, prefs)
        self._visible = devices
        show_percent = prefs.tray_show_percent

        if not devices:
            # 一台可见设备都没有：保留一枚灰色占位图标，应用仍能从托盘打开
            self._renders = {}
            self._ensure_icons(None)
            self._icons[0].setIcon(battery_icon(None, theme.ACCENT_BLUE, offline=True))
            self._icons[0].setToolTip(f"{APP_DISPLAY_NAME} · 未检测到设备")
            return

        renders: dict[str, tuple[object, str]] = {}
        for cfg in devices:
            snap = snapshots.get(cfg.id)
            if snap is None:
                # 还没扫到：先占好槽位，避免稍后补建导致顺序漂移
                renders[cfg.id] = (
                    battery_icon(None, prefs.color_for_device(cfg), False, show_percent, True),
                    f"{cfg.display_name}  --  等待首次扫描",
                )
                continue
            renders[cfg.id] = (
                battery_icon(
                    snap.display_level,
                    prefs.color_for_device(cfg),
                    bool(snap.display_charging),
                    show_percent,
                    not snap.online,
                ),
                self._device_line(cfg.display_name, snap),
            )
        self._renders = renders
        # 传入 id 序列：顺序变化时整组换血，让 Windows 槽位跟着主界面重排
        self._ensure_icons([d.id for d in devices])
        self._apply_renders()

    def _device_line(self, name: str, snap: Snapshot) -> str:
        level = "--" if snap.display_level is None else f"{snap.display_level}%"
        return f"{name}  {level}  {snap.status_text(self.store.prefs.low_battery_threshold)}"

    def _tooltip(self) -> str:
        lines = []
        for cfg in getattr(self, "_visible", self.registry.enabled_devices()):
            snap = self._snapshots.get(cfg.id)
            if snap:
                lines.append(self._device_line(cfg.display_name, snap))
        return "\n".join(lines) or APP_DISPLAY_NAME

    # -- 菜单 ---------------------------------------------------------------
    def _populate_menu(self) -> None:
        self._menu.clear()
        prefs = self.store.prefs
        for cfg in getattr(self, "_visible", self.registry.enabled_devices()):
            snap = self._snapshots.get(cfg.id)
            line = f"{cfg.display_name}  --  等待首次扫描" if snap is None else self._device_line(cfg.display_name, snap)
            action = QAction(line, self._menu)
            action.setIcon(
                battery_icon(
                    None if snap is None else snap.display_level,
                    prefs.color_for_device(cfg),
                    False if snap is None else bool(snap.display_charging),
                    True,
                    True if snap is None else not snap.online,
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
        self._create_timer.stop()
        self._swap_pending = False
        self._create_queue = []
        self._created = {}
        self._renders = {}
        self._teardown(self._icons)
        self._teardown(self._swap_old_icons)
        self._icons = []
        self._swap_old_icons = []
        self._icon_ids = []
        self._target_ids = []
