"""柔光玻璃实时跟随（问题2）的测试。

验证：
  1. Preferences 能读写 live_glass 开关；
  2. Panel 拖动时启动 _drag_update_timer，让玻璃按新位置重绘；
  3. 固定背景模式下只取一次，不再周期性重取。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QMouseEvent

from app.core.config import Preferences


pytestmark = pytest.mark.usefixtures("qapp")


def test_preferences_live_glass_defaults_true():
    prefs = Preferences()
    assert prefs.live_glass is True


def test_preferences_live_glass_read_from_dict(tmp_path):
    from app.core.config import DeviceRegistry
    registry = DeviceRegistry(user_dir=tmp_path)
    prefs = Preferences.from_dict({"live_glass": False}, registry)
    assert prefs.live_glass is False


def test_panel_drag_starts_update_timer(monkeypatch, tmp_path):
    """鼠标按下时，实时玻璃模式应启动拖动刷新定时器。"""
    from app.core.config import DeviceRegistry, SettingsStore
    from app.core.monitor import Monitor
    from app.ui.panel import Panel
    from app.ui.tray import TrayController

    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    monitor = Monitor(registry, store.prefs)
    tray = TrayController(registry, store)

    panel = Panel(registry, store, monitor, tray)

    # 默认开启实时玻璃
    assert panel.store.prefs.live_glass is True
    assert not panel._drag_update_timer.isActive()

    pos = QPoint(10, 10)
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        pos,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    panel.eventFilter(panel, event)

    assert panel._drag_update_timer.isActive()

    # 释放后应停止
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        pos,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    panel.eventFilter(panel, release)
    assert not panel._drag_update_timer.isActive()


def test_panel_drag_does_not_start_timer_when_live_glass_off(monkeypatch, tmp_path):
    """关闭实时玻璃后，拖动不应启动高频率刷新定时器。"""
    from app.core.config import DeviceRegistry, SettingsStore
    from app.core.monitor import Monitor
    from app.ui.panel import Panel
    from app.ui.tray import TrayController

    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    store.prefs.live_glass = False
    monitor = Monitor(registry, store.prefs)
    tray = TrayController(registry, store)

    panel = Panel(registry, store, monitor, tray)
    assert panel.store.prefs.live_glass is False

    pos = QPoint(10, 10)
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        pos,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    panel.eventFilter(panel, event)
    assert not panel._drag_update_timer.isActive()


def test_fixed_backdrop_skips_repeated_capture(monkeypatch, tmp_path):
    """live_glass=False 且已有背景时，_refresh_backdrop 不调用 capture_screen。"""
    from app.core.config import DeviceRegistry, SettingsStore
    from app.core.monitor import Monitor
    from app.ui.panel import Panel
    from app.ui.tray import TrayController
    from app.ui import backdrop

    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    store.prefs.live_glass = False
    monitor = Monitor(registry, store.prefs)
    tray = TrayController(registry, store)

    panel = Panel(registry, store, monitor, tray)
    panel._has_backdrop = True

    captured = []
    monkeypatch.setattr(backdrop, "capture_screen", lambda *args, **kwargs: captured.append(1) or None)

    panel._refresh_backdrop()
    assert captured == []
