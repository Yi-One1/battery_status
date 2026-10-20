"""主面板：无边框柔光玻璃窗 + 设备卡片网格 + 设置页 + 托盘联动。"""
from __future__ import annotations

import ctypes
import logging
import time
from typing import Optional

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.config import BACKGROUND_LIVE_GLASS, BACKGROUND_WHITE, DeviceRegistry, SettingsStore
from ..core.discovery import build_device_config
from ..core.model import Snapshot
from ..core.monitor import Monitor
from ..core.paths import resource_root
from ..core.presence import filter_visible, hidden_devices, ordered_devices
from . import theme
from .backdrop import capture_screen
from .cards import DeviceCard
from .glass import GlassSurface, IconButton, apply_shadow, clear_backdrop, paint_glass, set_backdrop
from .settings import SettingsPane
from .tray import TrayController

log = logging.getLogger(__name__)

MARGIN = 12          # 阴影留白，改小以减少截图软件识别的窗口边界与实际内容的偏差
CONTENT_WIDTH = 420
HEADER_HEIGHT = 58
# 面板显示期间重取桌面底图的间隔。取整屏 + 1/8 缩放约 20~30 ms，
# 15 秒一次摊下来对 CPU 的影响可以忽略，但换壁纸能在一刻钟内跟上。
BACKDROP_REFRESH_MS = 15_000
# 外壳的不透明度：取到桌面底图时让壁纸透上来；取不到就用实一点的静态玻璃
SHELL_ALPHA_WITH_BACKDROP = 0.74
SHELL_ALPHA_STATIC = 0.86


def shell_alpha(has_backdrop: bool) -> float:
    return SHELL_ALPHA_WITH_BACKDROP if has_backdrop else SHELL_ALPHA_STATIC
FOOTER_HEIGHT = 42
CARD_HEIGHT = 152
CARD_GAP = 12
PAGE_PADDING = 20


class _Shell(GlassSurface):
    """窗体内层：整块大圆角玻璃。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, radius=30, alpha=SHELL_ALPHA_WITH_BACKDROP)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # 纯白模式整块不透明，由 _apply_window_blur 依据背景模式开关
        self.solid = False

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        paint_glass(
            painter,
            rect,
            self.radius,
            alpha=self.base_alpha,
            border_alpha=0.92,
            offset=self.backdrop_offset(),
            solid=self.solid,
        )
        painter.end()


class Panel(QWidget):
    """托盘应用的浮动面板。"""

    hidden = Signal()

    def __init__(self, registry: DeviceRegistry, store: SettingsStore, monitor: Monitor, tray: TrayController) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.registry = registry
        self.store = store
        self.monitor = monitor
        self.tray = tray
        self._cards: dict[str, DeviceCard] = {}
        self._snapshots: dict[str, Snapshot] = {}
        self._notified: dict[str, float] = {}
        self._drag_offset: Optional[QPoint] = None
        self._settings_open = False
        self._first_show = True
        self._shown_at = 0.0
        self._has_backdrop = False
        self._build()
        self.monitor.discovered.connect(self._on_discovered)

        self._hotplug = QTimer(self)
        self._hotplug.setSingleShot(True)
        self._hotplug.setInterval(1200)
        self._hotplug.timeout.connect(self._on_hotplug)

        # 显示期间定时重取桌面底图：换壁纸 / 别的窗口移到面板后面时，
        # 玻璃里的颜色会跟着变，而不是停在打开那一刻。隐藏时停掉，不占 CPU。
        self._backdrop_timer = QTimer(self)
        self._backdrop_timer.setInterval(BACKDROP_REFRESH_MS)
        self._backdrop_timer.timeout.connect(self._refresh_backdrop)

        # 拖动时实时刷新玻璃：move 事件很密，用 16ms 定时器节流到约 60fps
        self._drag_update_timer = QTimer(self)
        self._drag_update_timer.setInterval(16)
        self._drag_update_timer.setSingleShot(False)
        self._drag_update_timer.timeout.connect(self._on_drag_update)

    # ------------------------------------------------------------------ 构建
    def _build(self) -> None:
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("外设电量")
        icon_path = resource_root() / "app" / "assets" / "app.ico"
        if icon_path.exists():
            from PySide6.QtGui import QIcon

            self.setWindowIcon(QIcon(str(icon_path)))

        root = QVBoxLayout(self)
        root.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        root.setSpacing(0)

        self.shell = _Shell(self)
        apply_shadow(self.shell, blur=28, dy=10, alpha=40)
        root.addWidget(self.shell)

        column = QVBoxLayout(self.shell)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        column.addWidget(self._build_header())

        self.stack = QStackedWidget(self.shell)
        self.stack.setStyleSheet("background: transparent;")
        self.stack.addWidget(self._build_devices_page())
        self.settings = SettingsPane(self.registry, self.store, self.stack)
        self.settings.changed.connect(self._on_settings_changed)
        self.settings.devices_changed.connect(self._on_devices_changed)
        self.stack.addWidget(self.settings)
        column.addWidget(self.stack, 1)

        column.addWidget(self._build_footer())

        self._resize_for_rows()
        self._apply_window_blur()
        QTimer.singleShot(0, self._place_initial)

    def _build_header(self) -> QWidget:
        header = QWidget(self.shell)
        header.setFixedHeight(HEADER_HEIGHT)
        header.setStyleSheet("background: transparent;")
        header.installEventFilter(self)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(PAGE_PADDING, 14, 14, 4)
        layout.setSpacing(6)

        text_box = QVBoxLayout()
        text_box.setSpacing(0)
        text_box.setContentsMargins(0, 0, 0, 0)

        self.title = QLabel("电量中心", header)
        self.title.setFont(theme.font(17, 600, theme.DISPLAY_FAMILIES, -0.2))
        self.title.setStyleSheet(f"color: {theme.TEXT_PRIMARY}; background: transparent;")
        text_box.addWidget(self.title)

        self.subtitle = QLabel("正在扫描…", header)
        self.subtitle.setFont(theme.font(10.5, 400))
        self.subtitle.setStyleSheet(f"color: {theme.TEXT_TERTIARY}; background: transparent;")
        self.subtitle.setMinimumWidth(0)
        self.subtitle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text_box.addWidget(self.subtitle)
        layout.addLayout(text_box, 1)

        self.refresh_btn = IconButton("refresh", header, diameter=28)
        self.refresh_btn.setToolTip("立即刷新")
        self.refresh_btn.clicked.connect(self._on_refresh)
        layout.addWidget(self.refresh_btn)

        self.gear_btn = IconButton("gear", header, diameter=28)
        self.gear_btn.setToolTip("设置")
        self.gear_btn.clicked.connect(self._toggle_settings)
        layout.addWidget(self.gear_btn)

        self.close_btn = IconButton("close", header, diameter=28)
        self.close_btn.setToolTip("收起到托盘")
        self.close_btn.clicked.connect(self.hide)
        layout.addWidget(self.close_btn)
        return header

    def _build_devices_page(self) -> QWidget:
        page = QWidget(self.shell)
        page.setStyleSheet("background: transparent;")
        self.grid = QGridLayout(page)
        self.grid.setContentsMargins(PAGE_PADDING, 4, PAGE_PADDING, 8)
        self.grid.setHorizontalSpacing(CARD_GAP)
        self.grid.setVerticalSpacing(CARD_GAP)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1)
        self._page = page
        self._rebuild_cards()
        return page

    def _build_footer(self) -> QWidget:
        footer = QWidget(self.shell)
        footer.setFixedHeight(FOOTER_HEIGHT)
        footer.setStyleSheet("background: transparent;")

        layout = QHBoxLayout(footer)
        layout.setContentsMargins(PAGE_PADDING, 2, PAGE_PADDING, 14)
        layout.setSpacing(8)

        self.footer_text = QLabel("尚未同步", footer)
        self.footer_text.setFont(theme.font(10.5, 400))
        self.footer_text.setStyleSheet(f"color: {theme.TEXT_TERTIARY}; background: transparent;")
        layout.addWidget(self.footer_text)
        layout.addStretch(1)

        self.chip = QLabel("", footer)
        self.chip.setFont(theme.font(10, 600))
        self.chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chip.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; background: rgba(120,120,128,0.10);"
            "border-radius: 9px; padding: 3px 9px;"
        )
        self.chip.hide()
        layout.addWidget(self.chip)
        return footer

    # ------------------------------------------------------------- 卡片布局
    def _card_width(self) -> int:
        return (CONTENT_WIDTH - 2 * PAGE_PADDING - CARD_GAP) // 2

    def visible_devices(self) -> list:
        """当前该占位的设备：已启用、未因断连收起，并按用户自定义顺序排列。"""
        visible = filter_visible(self.registry.enabled_devices(), self._snapshots, self.store.prefs)
        return ordered_devices(visible, self.store.prefs)

    def _rebuild_cards(self) -> None:
        for card in self._cards.values():
            self.grid.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()

        devices = self.visible_devices()
        for index, cfg in enumerate(devices):
            type_cfg = self.registry.type_of(cfg)
            card = DeviceCard(
                cfg.id,
                cfg.display_name,
                type_cfg.label,
                self.store.prefs.color_for_device(cfg),
                self.store.prefs.style_for_device(cfg),
                self._page,
            )
            row, col = divmod(index, 2)
            self.grid.addWidget(card, row, col)
            self._cards[cfg.id] = card
            snapshot = self._snapshots.get(cfg.id)
            if snapshot:
                card.apply_snapshot(snapshot, self.store.prefs.low_battery_threshold)
        self._resize_for_rows()

    def _sync_visibility(self) -> None:
        """断连超时的设备该收起、重新上线的该回来 —— 只有集合真的变了才重建界面。"""
        expected = [cfg.id for cfg in self.visible_devices()]
        if expected != list(self._cards.keys()):
            log.debug("可见设备变化：%s -> %s", list(self._cards.keys()), expected)
            self._rebuild_cards()

    def _resize_for_rows(self) -> None:
        rows = max(1, (len(self._cards) + 1) // 2)
        if self._settings_open:
            # 设置页内容较长（设备清单 + 各类外观），临时拉高窗口，减少滚动
            page_height = 540
        else:
            page_height = rows * CARD_HEIGHT + (rows - 1) * CARD_GAP + 12
        height = HEADER_HEIGHT + page_height + FOOTER_HEIGHT
        self.shell.setFixedSize(CONTENT_WIDTH, height)
        self.setFixedSize(CONTENT_WIDTH + 2 * MARGIN, height + 2 * MARGIN)

    # --------------------------------------------------------------- 位置/拖拽
    def _keep_in_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = min(max(area.left() + 8, self.x()), area.right() - self.width() - 8)
        y = min(max(area.top() + 8, self.y()), area.bottom() - self.height() - 8)
        self.move(x, y)

    def _place_initial(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = area.right() - self.width() - 10
        y = area.bottom() - self.height() - 10
        self.move(max(area.left(), x), max(area.top(), y))

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            if self.store.prefs.background_mode == BACKGROUND_LIVE_GLASS:
                self._drag_update_timer.start()
            return False
        if event.type() == QEvent.Type.MouseMove and self._drag_offset is not None:
            if event.buttons() & Qt.MouseButton.LeftButton:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                return False
        if event.type() == QEvent.Type.MouseButtonRelease:
            self._drag_offset = None
            self._drag_update_timer.stop()
        return super().eventFilter(obj, event)

    def _on_drag_update(self) -> None:
        """拖动期间持续重绘，让柔光玻璃按新位置从整屏背景板里取样。"""
        if self._drag_offset is not None and self.isVisible():
            self.shell.update()

    # ------------------------------------------------------------------ 显示
    def show_panel(self) -> None:
        if self._first_show:
            self._place_initial()
            self._first_show = False
        if not self.isVisible():
            # 必须在 show() 之前截屏，否则截到的是自己
            self._refresh_backdrop()
            self.show()
        self.raise_()
        self.activateWindow()
        # 无论上次关闭时停在设置页还是电量页，打开时一律回到电量显示页；
        # 托盘菜单里的「设置…」走 open_settings()，会在此之后重新切到设置页。
        if self._settings_open:
            self._toggle_settings()
        self.monitor.refresh()

    def _refresh_backdrop(self) -> None:
        """根据背景模式取桌面底图或回落静态背景。

        live_glass: 取整屏并做柔光玻璃自适应；拖动时颜色跟着走。
        white: 不截屏，直接使用纯白色背景（最省电、最稳定）。
        """
        mode = self.store.prefs.background_mode
        if mode == BACKGROUND_WHITE:
            clear_backdrop()
            self._has_backdrop = False
            self._apply_window_blur()
            return

        if mode == BACKGROUND_LIVE_GLASS and self._has_backdrop:
            # 柔光玻璃模式下已经取过底图就不再重复取，靠定时器刷新
            return

        exclude = self.frameGeometry().adjusted(-6, -6, 6, 6) if self.isVisible() else None
        backdrop = capture_screen(exclude=exclude)
        if backdrop is None or not backdrop.is_valid():
            if not self._has_backdrop:
                clear_backdrop()
                self._apply_window_blur()
            return
        set_backdrop(
            backdrop.pixmap,
            backdrop.palette,
            origin=backdrop.origin,
            scale=backdrop.scale,
        )
        self._has_backdrop = True
        log.debug(
            "背景取样完成：亮度 %.2f，环境色 %s，原点 %s，1/%d 缩放",
            backdrop.palette.luminance,
            backdrop.palette.color.name(),
            backdrop.origin,
            int(backdrop.scale),
        )
        self._apply_window_blur()

    def open_settings(self) -> None:
        self.show_panel()
        if not self._settings_open:
            self._toggle_settings()

    def toggle_panel(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show_panel()

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()
        self.hide()

    def event(self, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.WindowDeactivate:
            QTimer.singleShot(160, self._maybe_auto_hide)
        return super().event(event)

    def _maybe_auto_hide(self) -> None:
        if not self.isVisible():
            return
        # 刚弹出时（点击托盘、菜单收起）会出现瞬时失焦，别误关
        if time.monotonic() - self._shown_at < 0.5:
            QTimer.singleShot(280, self._maybe_auto_hide)
            return
        if QApplication.activeModalWidget() or QApplication.activePopupWidget():
            return
        if QApplication.activeWindow() is self:
            return
        self.hide()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            if self._settings_open:
                self._toggle_settings()
            else:
                self.hide()
            return
        super().keyPressEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._shown_at = time.monotonic()
        self._apply_window_blur()
        mode = self.store.prefs.background_mode
        # 白色模式不截屏、不刷新；柔光玻璃模式定时重取
        if mode == BACKGROUND_LIVE_GLASS:
            self._backdrop_timer.start()
        elif mode == BACKGROUND_WHITE:
            self._backdrop_timer.stop()
        if not self._has_backdrop and mode != BACKGROUND_WHITE:
            self._refresh_backdrop()


    def hideEvent(self, event) -> None:  # noqa: N802
        # 隐藏后没必要再截屏 —— 低占用的一部分
        self._backdrop_timer.stop()
        self._drag_update_timer.stop()
        super().hideEvent(event)

    def _apply_window_blur(self) -> None:
        """根据背景模式决定外壳画法：

        - 纯白色：整块不透明实心白（solid），与桌面彻底隔开。
        - 柔光玻璃：有底图时让壁纸透上来，无底图时更实一些。
        """
        mode = self.store.prefs.background_mode
        if mode == BACKGROUND_WHITE:
            self.shell.solid = True
            self.shell.base_alpha = 1.0
        else:
            self.shell.solid = False
            self.shell.base_alpha = shell_alpha(self._has_backdrop)
        self.shell.update()

    # ------------------------------------------------------------- 设备热插拔
    def nativeEvent(self, event_type, message):  # noqa: N802
        try:
            if event_type in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == 0x0219:  # WM_DEVICECHANGE
                    self._hotplug.start()
        except Exception:  # noqa: BLE001
            pass
        return super().nativeEvent(event_type, message)

    def _on_hotplug(self) -> None:
        log.debug("检测到设备变化，触发刷新")
        self.monitor.refresh()

    # ------------------------------------------------------------------ 交互
    def _on_refresh(self) -> None:
        self.footer_text.setText("正在刷新…")
        self.monitor.refresh()

    def _toggle_settings(self) -> None:
        self._settings_open = not self._settings_open
        self.stack.setCurrentWidget(self.settings if self._settings_open else self._page)
        self.gear_btn.set_kind("back" if self._settings_open else "gear")
        self.gear_btn.setToolTip("返回" if self._settings_open else "设置")
        self.title.setText("设置" if self._settings_open else "电量中心")
        self._resize_for_rows()
        if not self._first_show:
            self._keep_in_screen()
        if self._settings_open:
            self.footer_text.setText("改动即时保存并生效")
            self.chip.hide()
        elif self._snapshots:
            self._update_footer(self._snapshots)
        if self._settings_open:
            self._update_subtitle()

    def _on_settings_changed(self) -> None:
        self.settings.sync_colors()
        for cfg in self.registry.enabled_devices():
            card = self._cards.get(cfg.id)
            if card:
                # 改名立即生效：卡片标题跟随设置页，不用等卡片重建
                card.set_display_name(cfg.display_name)
                card.set_color(self.store.prefs.color_for_device(cfg))
                card.set_style(self.store.prefs.style_for_device(cfg))
        self.shell.setFixedSize(self.shell.width(), self.shell.height())
        self._resize_for_rows()
        # 背景模式切换时立即生效
        mode = self.store.prefs.background_mode
        if mode == BACKGROUND_WHITE:
            self._backdrop_timer.stop()
        elif mode == BACKGROUND_LIVE_GLASS:
            self._backdrop_timer.start()
        self._refresh_backdrop()
        self._apply_window_blur()
        self.monitor.set_prefs(self.store.prefs)
        if self._snapshots:
            self.apply_snapshots(self._snapshots)
        else:
            self._sync_visibility()
        self.tray.update_snapshots(self._snapshots)

    def _on_devices_changed(self) -> None:
        """设置页新增/删除了设备。"""
        self._rebuild_cards()
        self._resize_for_rows()
        if not self._first_show:
            self._keep_in_screen()
        self._update_subtitle()
        self._update_footer(self._snapshots)
        self.tray.update_snapshots(self._snapshots)
        self.monitor.refresh()

    def _on_discovered(self, devices: list) -> None:
        """监测线程扫到未登记的设备 —— 配置落盘只能在 UI 线程做，所以在这里收口。"""
        added = []
        for device in devices:
            base_id = device.default_id
            if self.registry.is_removed(base_id) or self.registry.get(base_id) is not None:
                continue
            cfg = build_device_config(device, base_id)
            self.registry.add_device(cfg)
            added.append(cfg)
        if not added:
            return
        log.info("自动登记 %d 台设备：%s", len(added), ", ".join(c.display_name for c in added))
        self.settings.refresh_devices()
        self._on_devices_changed()

    # ------------------------------------------------------------------ 数据
    def apply_snapshots(self, snapshots: dict[str, Snapshot]) -> None:
        self._snapshots = snapshots
        self._sync_visibility()
        threshold = self.store.prefs.low_battery_threshold
        for device_id, snapshot in snapshots.items():
            card = self._cards.get(device_id)
            if card:
                card.apply_snapshot(snapshot, threshold)
        self._update_footer(snapshots)
        self._update_subtitle(snapshots)
        self._maybe_notify(snapshots)

    def _update_subtitle(self, snapshots: Optional[dict[str, Snapshot]] = None) -> None:
        if self._settings_open:
            self.subtitle.setText("设备清单、颜色、样式与启动项")
            return
        snapshots = snapshots if snapshots is not None else self._snapshots
        visible_ids = set(self._cards)
        total = len(self._cards)
        online = sum(1 for did, s in snapshots.items() if did in visible_ids and s.online)
        read = sum(1 for did, s in snapshots.items() if did in visible_ids and s.level is not None)
        hidden = len(hidden_devices(self.registry.enabled_devices(), snapshots, self.store.prefs))
        if total == 0:
            self.subtitle.setText(f"{hidden} 台设备均已断开" if hidden else "没有启用的设备")
            return
        text = f"{total} 台设备 · {online} 台在线 · {read} 台可读电量"
        if hidden:
            text += f" · {hidden} 台已收起"
        self.subtitle.setText(text)

    def _update_footer(self, snapshots: dict[str, Snapshot]) -> None:
        recent = max((s.updated_at for s in snapshots.values() if s.updated_at), default=0.0)
        if recent:
            age = int(time.time() - recent)
            age_text = "刚刚" if age < 10 else f"{age // 60} 分钟前" if age >= 60 else f"{age} 秒前"
            self.footer_text.setText(f"最后同步 {age_text} · 每 {self.store.prefs.refresh_seconds} 秒刷新")
        else:
            self.footer_text.setText("等待首次同步…")

        if self.store.prefs.demo:
            self.chip.setText("演示数据")
            self.chip.setStyleSheet(
                f"color: {theme.WARN_TEXT}; background: rgba(255,159,10,0.18);"
                "border-radius: 9px; padding: 3px 9px;"
            )
            self.chip.show()
            return

        visible_ids = set(self._cards)
        total = len(self._cards)
        read = sum(1 for did, s in snapshots.items() if did in visible_ids and s.level is not None)
        if total and read < total:
            self.chip.setText(f"{total - read} 台暂无电量")
            self.chip.setStyleSheet(
                f"color: {theme.TEXT_SECONDARY}; background: rgba(120,120,128,0.10);"
                "border-radius: 9px; padding: 3px 9px;"
            )
            self.chip.show()
            self.chip.setToolTip("把鼠标停在对应卡片上可以看到诊断信息")
        elif total:
            self.chip.setText("全部已连接")
            self.chip.setStyleSheet(
                f"color: {theme.OK}; background: rgba(47,196,138,0.14);"
                "border-radius: 9px; padding: 3px 9px;"
            )
            self.chip.show()
        else:
            self.chip.hide()

    def _maybe_notify(self, snapshots: dict[str, Snapshot]) -> None:
        if not self.store.prefs.notifications:
            return
        threshold = self.store.prefs.low_battery_threshold
        now = time.time()
        for device_id, snapshot in snapshots.items():
            if not snapshot.online or snapshot.level is None or snapshot.charging:
                continue
            if snapshot.level > threshold:
                continue
            last = self._notified.get(device_id, 0.0)
            if now - last < 1800:
                continue
            cfg = self.registry.get(device_id)
            if cfg is None:
                continue
            self._notified[device_id] = now
            self.tray.notify(f"{cfg.display_name} 电量偏低", f"当前 {snapshot.level}%，建议及时充电。")
