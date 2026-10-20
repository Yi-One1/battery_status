"""窗口背景模式测试。

验证：
  1. Preferences 能正确读写并迁移 background_mode；
  2. 柔光玻璃模式下拖动会启动重绘定时器，白色/高斯模糊模式不启动；
  3. 白色模式不截屏，柔光玻璃已取到背景后不再重复截屏。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QMouseEvent

from app.core.config import (
    BACKGROUND_GAUSSIAN_BLUR,
    BACKGROUND_LIVE_GLASS,
    BACKGROUND_WHITE,
    Preferences,
)


pytestmark = pytest.mark.usefixtures("qapp")


def test_preferences_background_mode_defaults_to_live_glass():
    prefs = Preferences()
    assert prefs.background_mode == BACKGROUND_LIVE_GLASS


def test_preferences_live_glass_migrates_to_background_mode(tmp_path):
    from app.core.config import DeviceRegistry

    registry = DeviceRegistry(user_dir=tmp_path)
    prefs = Preferences.from_dict({"live_glass": False}, registry)
    assert prefs.background_mode == BACKGROUND_WHITE

    prefs2 = Preferences.from_dict({"live_glass": True}, registry)
    assert prefs2.background_mode == BACKGROUND_LIVE_GLASS


def test_preferences_background_mode_roundtrip(tmp_path):
    from app.core.config import DeviceRegistry, SettingsStore

    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    store.prefs.background_mode = BACKGROUND_GAUSSIAN_BLUR
    store.save()

    again = SettingsStore(registry, path=tmp_path / "preferences.json")
    assert again.prefs.background_mode == BACKGROUND_GAUSSIAN_BLUR


def test_panel_drag_starts_update_timer_in_live_glass(monkeypatch, tmp_path):
    """鼠标按下时，柔光玻璃模式应启动拖动刷新定时器。"""
    from app.core.config import DeviceRegistry, SettingsStore
    from app.core.monitor import Monitor
    from app.ui.panel import Panel
    from app.ui.tray import TrayController

    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    store.prefs.background_mode = BACKGROUND_LIVE_GLASS
    monitor = Monitor(registry, store.prefs)
    tray = TrayController(registry, store)

    panel = Panel(registry, store, monitor, tray)
    assert panel.store.prefs.background_mode == BACKGROUND_LIVE_GLASS
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


def test_panel_drag_does_not_start_timer_in_white_mode(monkeypatch, tmp_path):
    """白色模式下拖动不应启动高频率刷新定时器。"""
    from app.core.config import DeviceRegistry, SettingsStore
    from app.core.monitor import Monitor
    from app.ui.panel import Panel
    from app.ui.tray import TrayController

    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    store.prefs.background_mode = BACKGROUND_WHITE
    monitor = Monitor(registry, store.prefs)
    tray = TrayController(registry, store)

    panel = Panel(registry, store, monitor, tray)

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


def test_white_mode_skips_capture(monkeypatch, tmp_path):
    """白色模式下 _refresh_backdrop 不调用 capture_screen。"""
    from app.core.config import DeviceRegistry, SettingsStore
    from app.core.monitor import Monitor
    from app.ui import backdrop
    from app.ui.panel import Panel
    from app.ui.tray import TrayController

    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    store.prefs.background_mode = BACKGROUND_WHITE
    monitor = Monitor(registry, store.prefs)
    tray = TrayController(registry, store)

    panel = Panel(registry, store, monitor, tray)

    captured = []
    monkeypatch.setattr(backdrop, "capture_screen", lambda *args, **kwargs: captured.append(1) or None)

    panel._refresh_backdrop()
    assert captured == []
    assert panel.store.prefs.background_mode == BACKGROUND_WHITE


def test_live_glass_skips_repeated_capture(monkeypatch, tmp_path):
    """柔光玻璃已取到背景后，_refresh_backdrop 不再重复截屏。"""
    from app.core.config import DeviceRegistry, SettingsStore
    from app.core.monitor import Monitor
    from app.ui import backdrop
    from app.ui.panel import Panel
    from app.ui.tray import TrayController

    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    store.prefs.background_mode = BACKGROUND_LIVE_GLASS
    monitor = Monitor(registry, store.prefs)
    tray = TrayController(registry, store)

    panel = Panel(registry, store, monitor, tray)
    panel._has_backdrop = True

    captured = []
    monkeypatch.setattr(backdrop, "capture_screen", lambda *args, **kwargs: captured.append(1) or None)

    panel._refresh_backdrop()
    assert captured == []
