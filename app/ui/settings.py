"""设置页：颜色 / 样式 / 托盘 / 刷新间隔 / 自启动。"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..core import autostart
from ..core.config import DeviceRegistry, SettingsStore
from . import theme
from .glass import ColorSwatch, Divider, SegmentedControl, ToggleSwitch

STYLE_OPTIONS = [("ring", "环形"), ("bar", "条状"), ("number", "数字")]


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

    def __init__(self, registry: DeviceRegistry, store: SettingsStore, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.registry = registry
        self.store = store
        self._building = True
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

        column.addWidget(self._section("每类外设的颜色与样式"))
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

        self.blur_toggle = ToggleSwitch()
        self.blur_toggle.setChecked(store.prefs.glass_blur)
        self.blur_toggle.toggled.connect(self._on_blur)
        column.addWidget(SettingRow("真实毛玻璃", "调用 DWM 模糊桌面背景，个别显卡驱动会有拖影", self.blur_toggle))

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

    # -- 交互 ---------------------------------------------------------------
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

    def _on_interval(self, value: int) -> None:
        self.interval_value.setText(f"{value}s")
        self.store.prefs.refresh_seconds = value
        self._commit(debounce_only=True)
        self._flush()

    def _on_threshold(self, value: int) -> None:
        self.threshold_value.setText(f"{value}%")
        self.store.prefs.low_battery_threshold = value
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

    def _on_blur(self, checked: bool) -> None:
        self.store.prefs.glass_blur = checked
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
