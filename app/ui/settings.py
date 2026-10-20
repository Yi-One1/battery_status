"""设置页：设备清单 / 颜色 / 样式 / 托盘 / 刷新间隔 / 自启动。"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core import autostart
from ..core.config import DeviceConfig, DeviceRegistry, SettingsStore
from . import theme
from .add_device import AddDeviceDialog
from .confirm import ConfirmDialog
from .glass import ColorSwatch, Divider, PillButton, SegmentedControl, ToggleSwitch
from .removed_devices import RemovedDevicesDialog

log = logging.getLogger(__name__)

STYLE_OPTIONS = [("ring", "环形"), ("bar", "条状"), ("number", "数字")]

# 自动收起断连设备的可选时长（分钟）
OFFLINE_OPTIONS = [("5", "5 分钟"), ("10", "10 分钟"), ("30", "30 分钟"), ("60", "1 小时")]

SOURCE_LABELS = {"builtin": "内置", "user": "手动添加", "auto": "自动发现"}


def _label(text: str, size: float = 12.0, weight: int = 500, color: str = theme.TEXT_PRIMARY) -> QLabel:
    label = QLabel(text)
    label.setFont(theme.font(size, weight))
    label.setStyleSheet(f"color: {color}; background: transparent;")
    return label


class SettingRow(QWidget):
    """左侧标题/说明，右侧控件。"""

    def __init__(self, title: str, subtitle: str = "", control: Optional[QWidget] = None) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(10)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(1)
        text_box.addWidget(_label(title, 12.0, 500))
        if subtitle:
            text_box.addWidget(_label(subtitle, 10.0, 400, theme.TEXT_TERTIARY))
        layout.addLayout(text_box, 1)
        if control is not None:
            layout.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)


class SettingsPane(QWidget):
    changed = Signal()
    devices_changed = Signal()

    def __init__(self, registry: DeviceRegistry, store: SettingsStore, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.registry = registry
        self.store = store
        self._building = True
        self._device_rows: dict[str, QWidget] = {}
        self._device_swatches: dict[str, ColorSwatch] = {}
        self._device_segments: dict[str, SegmentedControl] = {}
        self._device_hints: dict[str, QLabel] = {}
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 6, 0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setStyleSheet("background: transparent;")
        outer.addWidget(scroll)

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        scroll.setWidget(content)
        column = QVBoxLayout(content)
        column.setContentsMargins(20, 2, 14, 26)
        column.setSpacing(2)

        # ---- 设备清单（问题3）----
        column.addWidget(self._section("设备"))
        self._device_holder = QWidget()
        self._device_holder.setStyleSheet("background: transparent;")
        self._device_layout = QVBoxLayout(self._device_holder)
        self._device_layout.setContentsMargins(0, 0, 0, 0)
        self._device_layout.setSpacing(1)
        column.addWidget(self._device_holder)
        self._removed_button = PillButton("已移除设备", tone="neutral", height=22)
        self._removed_button.clicked.connect(self._open_removed_dialog)
        column.addWidget(SettingRow("已移除设备", "查看并恢复或彻底移除已删除的设备", self._removed_button))
        self._rebuild_device_rows()

        self.add_button = PillButton("添加设备…", tone="accent", height=26)
        self.add_button.setMinimumWidth(92)
        self.add_button.clicked.connect(self._open_add_dialog)
        column.addWidget(SettingRow("新增外设", "从检测到的设备里点选，或按 VID/PID 手动填写", self.add_button))

        self.auto_add_toggle = ToggleSwitch()
        self.auto_add_toggle.setChecked(store.prefs.auto_add)
        self.auto_add_toggle.toggled.connect(self._on_auto_add)
        column.addWidget(SettingRow("自动新增设备", "扫到未登记的外设时自动补一张卡片", self.auto_add_toggle))

        self.auto_hide_toggle = ToggleSwitch()
        self.auto_hide_toggle.setChecked(store.prefs.auto_hide_offline)
        self.auto_hide_toggle.toggled.connect(self._on_auto_hide)
        column.addWidget(SettingRow("自动收起断连设备", "断开超过设定时长就不再占位，重新连上会自动回来", self.auto_hide_toggle))

        self.offline_picker = SegmentedControl(OFFLINE_OPTIONS, height=24)
        self.offline_picker.setFixedWidth(202)
        self.offline_picker.set_value(self._offline_key(store.prefs.offline_hide_minutes))
        self.offline_picker.changed.connect(self._on_offline_minutes)
        self._offline_row = SettingRow("断连多久后收起", "只影响显示，不会删除配置", self.offline_picker)
        column.addWidget(self._offline_row)

        column.addWidget(Divider())

        column.addWidget(self._section("外观"))
        self.live_glass_toggle = ToggleSwitch()
        self.live_glass_toggle.setChecked(store.prefs.live_glass)
        self.live_glass_toggle.toggled.connect(self._on_live_glass)
        column.addWidget(SettingRow(
            "实时柔光玻璃",
            "窗口拖动时背景跟着走；关闭则固定为显示时的桌面背景",
            self.live_glass_toggle,
        ))

        column.addWidget(self._section("不单独设置时，按外设类型取默认外观"))
        for key, type_cfg in registry.types.items():
            column.addWidget(self._type_row(key, type_cfg.label, type_cfg.color))

        column.addWidget(Divider())
        column.addWidget(self._section("托盘"))
        self.tray_mode = SegmentedControl([("per-device", "每设备独立"), ("merged", "合并一个")])
        self.tray_mode.set_value(store.prefs.tray_mode)
        self.tray_mode.setFixedWidth(150)
        self.tray_mode.changed.connect(self._on_tray_mode)
        column.addWidget(SettingRow("托盘图标", "每个设备一个图标，或合并显示最低电量", self.tray_mode))

        self.tray_percent = ToggleSwitch()
        self.tray_percent.setChecked(store.prefs.tray_show_percent)
        self.tray_percent.toggled.connect(self._on_tray_percent)
        column.addWidget(SettingRow("图标内显示百分比", "图标更直观，但小尺寸下数字较挤", self.tray_percent))

        TRAY_ACTIONS = [("toggle", "切换显示"), ("show", "电量界面"), ("settings", "设置页")]
        self.tray_click = SegmentedControl(TRAY_ACTIONS, height=24)
        self.tray_click.set_value(store.prefs.tray_click_action)
        self.tray_click.setFixedWidth(202)
        self.tray_click.changed.connect(self._on_tray_click)
        column.addWidget(SettingRow("左键单击", "点击托盘图标后打开哪个界面", self.tray_click))

        self.tray_double_click = SegmentedControl(TRAY_ACTIONS, height=24)
        self.tray_double_click.set_value(store.prefs.tray_double_click_action)
        self.tray_double_click.setFixedWidth(202)
        self.tray_double_click.changed.connect(self._on_tray_double_click)
        column.addWidget(SettingRow("左键双击", "双击托盘图标后打开哪个界面", self.tray_double_click))

        column.addWidget(Divider())
        column.addWidget(self._section("监测与提醒"))

        self.interval_slider = QSlider(Qt.Orientation.Horizontal)
        self.interval_slider.setRange(10, 300)
        self.interval_slider.setSingleStep(10)
        self.interval_slider.setPageStep(30)
        self.interval_slider.setValue(store.prefs.refresh_seconds)
        self.interval_slider.setFixedWidth(120)
        self.interval_slider.valueChanged.connect(self._on_interval)
        self.interval_value = _label(f"{store.prefs.refresh_seconds}s", 11.0, 500, theme.TEXT_SECONDARY)
        self.interval_value.setFixedWidth(34)
        interval_box = QWidget()
        interval_layout = QHBoxLayout(interval_box)
        interval_layout.setContentsMargins(0, 0, 0, 0)
        interval_layout.setSpacing(6)
        interval_layout.addWidget(self.interval_slider)
        interval_layout.addWidget(self.interval_value)
        column.addWidget(SettingRow("刷新间隔", "越长越省电，电量变化本身很慢", interval_box))

        self.threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.threshold_slider.setRange(5, 60)
        self.threshold_slider.setValue(store.prefs.low_battery_threshold)
        self.threshold_slider.setFixedWidth(120)
        self.threshold_slider.valueChanged.connect(self._on_threshold)
        self.threshold_value = _label(f"{store.prefs.low_battery_threshold}%", 11.0, 500, theme.TEXT_SECONDARY)
        self.threshold_value.setFixedWidth(34)
        threshold_box = QWidget()
        threshold_layout = QHBoxLayout(threshold_box)
        threshold_layout.setContentsMargins(0, 0, 0, 0)
        threshold_layout.setSpacing(6)
        threshold_layout.addWidget(self.threshold_slider)
        threshold_layout.addWidget(self.threshold_value)
        column.addWidget(SettingRow("低电量阈值", "低于该值卡片与托盘图标会转为警示色", threshold_box))

        self.notify_toggle = ToggleSwitch()
        self.notify_toggle.setChecked(store.prefs.notifications)
        self.notify_toggle.toggled.connect(self._on_notify)
        column.addWidget(SettingRow("低电量气泡提醒", "仅在跌破阈值时提醒一次", self.notify_toggle))

        column.addWidget(Divider())
        column.addWidget(self._section("启动"))

        self.autostart_toggle = ToggleSwitch()
        self.autostart_toggle.setChecked(autostart.is_enabled())
        self.autostart_toggle.toggled.connect(self._on_autostart)
        column.addWidget(SettingRow("开机自启动", "写入当前用户的启动项，无需管理员权限", self.autostart_toggle))

        self.minimized_toggle = ToggleSwitch()
        self.minimized_toggle.setChecked(store.prefs.start_minimized)
        self.minimized_toggle.toggled.connect(self._on_start_minimized)
        column.addWidget(SettingRow("启动后直接进托盘", "不弹出面板，保持后台静默", self.minimized_toggle))

        self.demo_toggle = ToggleSwitch()
        self.demo_toggle.setChecked(store.prefs.demo)
        self.demo_toggle.toggled.connect(self._on_demo)
        column.addWidget(SettingRow("演示数据", "用模拟电量预览效果，界面上会有「演示」标记", self.demo_toggle))

        column.addStretch(1)
        self._building = False

    # -- 构造辅助 -----------------------------------------------------------
    @staticmethod
    def _section(text: str) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 10, 0, 2)
        layout.setSpacing(0)
        layout.addWidget(_label(text, 10.5, 600, theme.TEXT_TERTIARY))
        return box

    def _type_row(self, key: str, label: str, default_color: str) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(8)

        swatch = ColorSwatch(self.store.prefs.color_for(key))
        swatch.clicked.connect(lambda _=False, k=key, s=swatch: self._pick_color(k, s))
        self._swatches = getattr(self, "_swatches", {})
        self._swatches[key] = swatch

        layout.addWidget(_label(label, 12.0, 500), 0)
        layout.addStretch(1)
        layout.addWidget(swatch, 0, Qt.AlignmentFlag.AlignVCenter)

        segmented = SegmentedControl(STYLE_OPTIONS, height=24)
        segmented.setFixedWidth(138)
        segmented.set_value(self.store.prefs.style_for(key))
        segmented.changed.connect(lambda value, k=key: self._on_style(k, value))
        self._segments = getattr(self, "_segments", {})
        self._segments[key] = segmented
        layout.addWidget(segmented, 0, Qt.AlignmentFlag.AlignVCenter)
        return holder

    # -- 设备清单 -----------------------------------------------------------
    def device_widgets(self) -> dict[str, QWidget]:
        """当前展示的设备行（测试与调试用）。"""
        return dict(self._device_rows)

    def _sync_restore_button(self) -> None:
        removed = self.registry.removed_ids()
        if not hasattr(self, "_removed_button"):
            return
        self._removed_button.setText(f"已移除设备 ({len(removed)})" if removed else "已移除设备")
        self._removed_button.setEnabled(bool(removed))

    def _open_removed_dialog(self) -> None:
        """打开已移除设备列表弹窗。"""
        dialog = RemovedDevicesDialog(self.registry, self.window())
        dialog.exec()
        self._sync_restore_button()
        self._rebuild_device_rows()
        self.devices_changed.emit()

    def restore_removed(self) -> int:
        """撤销全部删除操作（兼容旧入口），返回恢复的数量。"""
        removed = self.registry.removed_ids()
        if not removed:
            return 0
        for device_id in removed:
            self.registry.restore_device(device_id, persist=False)
        self.registry.save_user_config()
        log.info("恢复 %d 台被移除的设备", len(removed))
        self._sync_restore_button()
        self._rebuild_device_rows()
        self.devices_changed.emit()
        return len(removed)

    def _rebuild_device_rows(self) -> None:
        while self._device_layout.count():
            item = self._device_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._device_rows.clear()
        self._device_swatches.clear()
        self._device_segments.clear()
        self._device_hints.clear()

        devices = self.registry.enabled_devices()
        if not devices:
            self._device_layout.addWidget(
                _label("清单为空。点右下角「添加设备…」挑一台。", 11.0, 400, theme.TEXT_TERTIARY)
            )
        else:
            for cfg in devices:
                row = self._device_row(cfg)
                self._device_layout.addWidget(row)
                self._device_rows[cfg.id] = row
        self._sync_restore_button()

    def _device_row(self, cfg: DeviceConfig) -> QWidget:
        """一台设备两行：上行名称 + 取色 + 移除，下行来源说明 + 样式选择。"""
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        outer = QVBoxLayout(holder)
        outer.setContentsMargins(0, 5, 0, 5)
        outer.setSpacing(2)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)

        name_edit = QLineEdit(cfg.display_name)
        name_edit.setFont(theme.font(12.0, 500))
        name_edit.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; background: transparent; border: none; padding: 0px;"
        )
        name_edit.editingFinished.connect(lambda did=cfg.id, ed=name_edit: self._on_device_rename(did, ed.text()))
        top.addWidget(name_edit, 1)

        dot = ColorSwatch(self.store.prefs.color_for_device(cfg), diameter=20)
        dot.setToolTip(f"「{cfg.display_name}」的颜色 · 当前 {self.store.prefs.color_for_device(cfg)}")
        dot.clicked.connect(lambda _=False, c=cfg, s=dot: self._pick_device_color(c, s))
        self._device_swatches[cfg.id] = dot
        top.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)

        remove = PillButton("移除", tone="neutral")
        remove.setToolTip(f"从清单里删除「{cfg.display_name}」")
        remove.clicked.connect(lambda _=False, did=cfg.id: self._confirm_remove(did))
        top.addWidget(remove, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(8)
        type_cfg = self.registry.type_of(cfg)
        source = SOURCE_LABELS.get(cfg.source, cfg.source)
        suffix = " · 已自定义" if self.store.prefs.has_device_override(cfg.id) else ""
        hint = _label(f"{type_cfg.label} · {source}{suffix}", 10.0, 400, theme.TEXT_TERTIARY)
        self._device_hints[cfg.id] = hint
        bottom.addWidget(hint, 1)

        segmented = SegmentedControl(STYLE_OPTIONS, height=22)
        segmented.setFixedWidth(126)
        segmented.set_value(self.store.prefs.style_for_device(cfg))
        segmented.changed.connect(lambda value, did=cfg.id: self._on_device_style(did, value))
        self._device_segments[cfg.id] = segmented
        bottom.addWidget(segmented, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(bottom)
        return holder

    def _pick_device_color(self, cfg: DeviceConfig, swatch: ColorSwatch) -> None:
        current = QColor(self.store.prefs.color_for_device(cfg))
        dialog = QColorDialog(current, self.window())
        dialog.setWindowTitle(f"「{cfg.display_name}」的颜色")
        dialog.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, False)
        if not dialog.exec():
            return
        chosen = dialog.currentColor()
        if not chosen.isValid() or not self.store.prefs.set_device_color(cfg.id, chosen.name()):
            return
        swatch.set_color(chosen.name())
        swatch.setToolTip(f"「{cfg.display_name}」的颜色 · 当前 {chosen.name()}")
        self._refresh_device_hint(cfg.id)
        self._commit()

    def _on_device_style(self, device_id: str, value: str) -> None:
        if not self.store.prefs.set_device_style(device_id, value):
            return
        self._refresh_device_hint(device_id)
        self._commit()

    def _on_device_rename(self, device_id: str, new_name: str) -> None:
        if not self.registry.rename_device(device_id, new_name):
            return
        # 主界面卡片会随 devices_changed 重建，设置页只更新当前行提示
        self._refresh_device_hint(device_id)
        self._commit()

    def reset_device_appearance(self, device_id: str) -> bool:
        """撤掉单台设备的自定义，回到所属类型的默认外观。"""
        if not self.store.prefs.has_device_override(device_id):
            return False
        self.store.prefs.reset_device(device_id)
        self._commit()
        self._rebuild_device_rows()
        return True

    def _refresh_device_hint(self, device_id: str) -> None:
        """只改那台设备的"已自定义"标记，避免整列表重建（重建会销毁正在被点击的控件）。"""
        label = self._device_hints.get(device_id)
        cfg = self.registry.get(device_id)
        if label is None or cfg is None:
            return
        type_cfg = self.registry.type_of(cfg)
        source = SOURCE_LABELS.get(cfg.source, cfg.source)
        suffix = " · 已自定义" if self.store.prefs.has_device_override(device_id) else ""
        label.setText(f"{type_cfg.label} · {source}{suffix}")

    def _confirm_remove(self, device_id: str) -> None:
        """移除前先问一句 —— 卡片和托盘图标会一起消失，不该一次误点就没了。"""
        cfg = self.registry.get(device_id)
        if cfg is None:
            return
        ok = ConfirmDialog.ask(
            self.window(),
            title="移除设备",
            message=f"确定要从清单里移除「{cfg.display_name}」吗？",
            detail="移除后卡片和托盘图标会一起消失。被移除的设备会留在本页底部，可以随时恢复。",
            confirm_text="移除",
            cancel_text="取消",
            tone="danger",
        )
        if ok:
            self.remove_device(device_id)

    def remove_device(self, device_id: str) -> bool:
        """删除设备（内置设备写墓碑，用户设备可再恢复）。"""
        cfg = self.registry.get(device_id)
        if cfg is None:
            return False
        if not self.registry.remove_device(device_id):
            return False
        log.info("用户移除设备 %s", device_id)
        self._rebuild_device_rows()
        self.devices_changed.emit()
        return True

    def refresh_devices(self) -> None:
        """外部（自动新增 / 删除）改动清单后回填。"""
        self._rebuild_device_rows()

    def _open_add_dialog(self) -> None:
        dialog = AddDeviceDialog(self.registry, self.window())
        dialog.device_added.connect(self._on_device_added)
        dialog.exec()

    def _on_device_added(self, cfg: DeviceConfig) -> None:
        log.info("新增设备 %s", cfg.id)
        self._rebuild_device_rows()
        self.devices_changed.emit()

    @staticmethod
    def _offline_key(minutes: int) -> str:
        options = [int(key) for key, _ in OFFLINE_OPTIONS]
        nearest = min(options, key=lambda value: abs(value - int(minutes)))
        return str(nearest)

    # -- 交互 ---------------------------------------------------------------
    def _on_auto_add(self, checked: bool) -> None:
        self.store.prefs.auto_add = checked
        self._commit()

    def _on_auto_hide(self, checked: bool) -> None:
        self.store.prefs.auto_hide_offline = checked
        self.offline_picker.setEnabled(checked)
        self._commit()

    def _on_offline_minutes(self, value: str) -> None:
        try:
            self.store.prefs.offline_hide_minutes = max(1, int(value))
        except (TypeError, ValueError):
            return
        self._commit()

    def _pick_color(self, key: str, swatch: ColorSwatch) -> None:
        current = QColor(self.store.prefs.color_for(key))
        dialog = QColorDialog(current, self.window())
        dialog.setWindowTitle("选择颜色")
        dialog.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, False)
        if dialog.exec():
            chosen = dialog.currentColor()
            if chosen.isValid():
                swatch.set_color(chosen.name())
                self.store.prefs.colors[key] = chosen.name()
                self._commit()

    def _on_style(self, key: str, value: str) -> None:
        self.store.prefs.styles[key] = value
        self._commit()

    def _on_tray_mode(self, value: str) -> None:
        self.store.prefs.tray_mode = value
        self._commit()

    def _on_tray_percent(self, checked: bool) -> None:
        self.store.prefs.tray_show_percent = checked
        self._commit()

    def _on_tray_click(self, value: str) -> None:
        self.store.prefs.tray_click_action = value
        self._commit()

    def _on_tray_double_click(self, value: str) -> None:
        self.store.prefs.tray_double_click_action = value
        self._commit()

    def _on_interval(self, value: int) -> None:
        self.interval_value.setText(f"{value}s")
        self.store.prefs.refresh_seconds = value
        self._commit(debounce_only=True)
        self._flush()

    def _on_threshold(self, value: int) -> None:
        self.threshold_value.setText(f"{value}%")
        self.store.prefs.low_battery_threshold = value
        self._commit()

    def _on_live_glass(self, checked: bool) -> None:
        self.store.prefs.live_glass = checked
        self._commit()

    def _on_notify(self, checked: bool) -> None:
        self.store.prefs.notifications = checked
        self._commit()

    def _on_autostart(self, checked: bool) -> None:
        autostart.apply(checked)
        self.store.prefs.autostart = checked
        self._commit()

    def _on_start_minimized(self, checked: bool) -> None:
        self.store.prefs.start_minimized = checked
        self._commit()

    def _on_demo(self, checked: bool) -> None:
        self.store.prefs.demo = checked
        self._commit()

    # -- 保存 ---------------------------------------------------------------
    def _commit(self, debounce_only: bool = False) -> None:
        if self._building:
            return
        self.store.save()
        if not debounce_only:
            self.changed.emit()

    def _flush(self) -> None:
        self.changed.emit()

    def sync_colors(self) -> None:
        """外部改动（如恢复默认）后回填控件。"""
        for key, swatch in getattr(self, "_swatches", {}).items():
            swatch.set_color(self.store.prefs.color_for(key))
        for key, segmented in getattr(self, "_segments", {}).items():
            segmented.set_value(self.store.prefs.style_for(key))
        # 单台设备的覆盖值：颜色圆点 + 样式分段都要跟着刷新
        for device_id, swatch in self._device_swatches.items():
            cfg = self.registry.get(device_id)
            if cfg is not None:
                swatch.set_color(self.store.prefs.color_for_device(cfg))
        for device_id, segmented in self._device_segments.items():
            cfg = self.registry.get(device_id)
            if cfg is not None:
                segmented.set_value(self.store.prefs.style_for_device(cfg))
            self._refresh_device_hint(device_id)
