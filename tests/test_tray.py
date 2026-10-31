"""托盘图标渲染测试。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module", autouse=True)
def _ensure_qapp():
    if QApplication.instance() is None:
        QApplication(["test"])


def _install_fake_tray(monkeypatch) -> list:
    """离屏环境替身：记录创建的托盘图标及其图标/提示文本，并把换血间隔归零。"""
    from PySide6.QtCore import QObject, Signal as QtSignal

    from app.ui import tray as tray_module

    monkeypatch.setattr(tray_module, "SWAP_DELAY_MS", 0)
    monkeypatch.setattr(tray_module, "CREATE_INTERVAL_MS", 0)

    created: list = []

    class _FakeTrayIcon(QObject):
        activated = QtSignal(object)

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self.icons: list = []
            self.tooltips: list = []
            created.append(self)

        def setContextMenu(self, menu) -> None:
            pass

        def show(self) -> None:
            pass

        def hide(self) -> None:
            pass

        def deleteLater(self) -> None:
            pass

        def setIcon(self, icon) -> None:
            self.icons.append(icon)

        def setToolTip(self, text) -> None:
            self.tooltips.append(text)

    monkeypatch.setattr(tray_module, "QSystemTrayIcon", _FakeTrayIcon)
    return created


def _settle(qapp, rounds: int = 40) -> None:
    """跑完换血 + 逐个注册两轮定时器（测试里间隔都归零）。"""
    for _ in range(rounds):
        qapp.processEvents()


def _capture_texts(monkeypatch) -> list[str]:
    """劫持 QPainter.drawText，返回绘制的所有文本。"""
    captured: list[str] = []
    original_drawText = QPainter.drawText

    def fake_drawText(painter, *args):
        # drawText(QRectF, int, str) 或 drawText(QPointF, str)
        if len(args) == 3 and isinstance(args[2], str):
            captured.append(args[2])
        elif len(args) == 2 and isinstance(args[1], str):
            captured.append(args[1])
        return original_drawText(painter, *args)

    monkeypatch.setattr(QPainter, "drawText", fake_drawText)
    return captured


def _paint_icon(kind_args: tuple) -> None:
    from app.ui.tray import _draw_icon

    level, color, charging, show_percent, offline = kind_args
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    _draw_icon(painter, 32, level, color, charging, show_percent, offline)
    painter.end()


def test_battery_icon_percent_mode_draws_number_without_percent_sign(monkeypatch):
    """托盘百分比模式只画数值，不画 % 符号。"""
    from app.ui.tray import _draw_icon

    captured = _capture_texts(monkeypatch)
    _paint_icon((83, "#0A84FF", False, True, False))

    assert any("83" in text for text in captured), f"绘制的文本不含电量数字：{captured}"
    assert not any("%" in text for text in captured), f"托盘图标不应绘制 % 符号：{captured}"


def test_battery_icon_charging_placeholder_draws_bolt_without_number(monkeypatch):
    """充电占位符（电量不可读）只画闪电，不画数字。"""
    from app.ui.tray import _draw_icon

    captured = _capture_texts(monkeypatch)
    _paint_icon((None, "#0A84FF", True, True, False))

    assert not captured, f"充电占位符不应绘制数字文本：{captured}"


def test_battery_icon_charging_with_level_draws_number(monkeypatch):
    """充电且电量可读：照常绘制数值（另加闪电路径，不经过 drawText）。"""
    from app.ui.tray import _draw_icon

    captured = _capture_texts(monkeypatch)
    _paint_icon((96, "#0A84FF", True, True, False))

    assert any("96" in text for text in captured), f"绘制的文本不含电量数字：{captured}"
    assert not any("%" in text for text in captured), f"托盘图标不应绘制 % 符号：{captured}"


def test_device_line_uses_display_level():
    """充电占位符（level=50 & charging）时，托盘提示应隐藏电量而不是显示 50%。"""
    from app.core.config import DeviceRegistry, SettingsStore
    from app.core.model import Snapshot
    from app.ui.tray import TrayController

    registry = DeviceRegistry()
    store = SettingsStore(registry)
    tray = TrayController(registry, store)

    placeholder = Snapshot(
        device_id="mchose-g98pro",
        level=50,
        charging=True,
        online=True,
        link="wireless",
    )
    line = tray._device_line("迈从 G98 PRO", placeholder)
    assert "50%" not in line
    assert "--" in line or "充电中" in line

    normal = Snapshot(
        device_id="mchose-g98pro",
        level=83,
        charging=False,
        online=True,
        link="wireless",
    )
    line = tray._device_line("迈从 G98 PRO", normal)
    assert "83%" in line


def test_tray_single_click_toggles_and_double_click_ignored(qapp):
    """单击只发 toggle 信号；双击功能已删除，DoubleClick 不触发任何动作。"""
    from PySide6.QtWidgets import QSystemTrayIcon

    from app.core.config import DeviceRegistry, SettingsStore
    from app.ui.tray import TrayController

    registry = DeviceRegistry()
    store = SettingsStore(registry)
    tray = TrayController(registry, store)

    events: list[str] = []
    tray.toggle_requested.connect(lambda: events.append("toggle"))
    tray.show_panel.connect(lambda: events.append("show"))
    tray.show_settings.connect(lambda: events.append("settings"))

    tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
    tray._on_activated(QSystemTrayIcon.ActivationReason.DoubleClick)

    assert events == ["toggle"], f"单击应只发一次 toggle，双击应被忽略：{events}"


def test_show_panel_always_lands_on_battery_page(qapp, tmp_path, monkeypatch):
    """无论上次关闭时停在设置页还是电量页，show_panel 一律回到电量显示页。"""
    from app.core.config import DeviceRegistry, SettingsStore
    from app.core.monitor import Monitor
    from app.ui.panel import Panel
    from app.ui.tray import TrayController

    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    monitor = Monitor(registry, store.prefs)
    tray = TrayController(registry, store)
    panel = Panel(registry, store, monitor, tray)
    monkeypatch.setattr(panel.monitor, "refresh", lambda: None)

    # 模拟上次关闭时停在设置页
    panel._settings_open = True
    panel.stack.setCurrentWidget(panel.settings)

    panel.show_panel()

    assert panel._settings_open is False
    assert panel.stack.currentWidget() is panel._page


def test_tray_builds_all_slots_before_first_scan(qapp, tmp_path, monkeypatch):
    """启动时按主界面顺序一次建齐槽位，首轮扫描回来后只换图、不重建。

    以前先用空快照建 1 枚占位、扫描回来再整组销毁重建 N 枚，
    Windows 会按注册先后排槽位，于是托盘顺序既跟主界面不一致、每次重启还会变。
    """
    from app.core.config import DeviceRegistry, SettingsStore
    from app.core.model import Snapshot
    from app.ui.tray import TrayController

    _install_fake_tray(monkeypatch)
    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    tray = TrayController(registry, store)

    ids = [d.id for d in registry.enabled_devices()]
    assert len(ids) >= 2

    tray.update_snapshots({})   # 首轮扫描还没回来
    _settle(qapp)
    assert tray._icon_ids == ids, "槽位顺序必须等于主界面顺序"
    assert all("等待首次扫描" in icon.tooltips[-1] for icon in tray._icons)

    first_icons = list(tray._icons)
    snapshots = {device_id: Snapshot(device_id=device_id, level=80, online=True) for device_id in ids}
    tray.update_snapshots(snapshots)
    _settle(qapp)

    assert list(tray._icons) == first_icons, "扫描回来只换图，不该重建图标"
    assert tray._icon_ids == ids
    assert all("80%" in icon.tooltips[-1] for icon in tray._icons)


def test_tray_registers_icons_one_by_one(qapp, tmp_path, monkeypatch):
    """逐个注册：一次只建一枚，避免 shell 收到批量的乱序注册。"""
    from app.core.config import DeviceRegistry, SettingsStore
    from app.core.model import Snapshot
    from app.ui import tray as tray_module
    from app.ui.tray import TrayController

    _install_fake_tray(monkeypatch)
    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    tray = TrayController(registry, store)
    ids = [d.id for d in registry.enabled_devices()]

    # 把注册间隔拉长，就能看清「一次只建一枚」
    monkeypatch.setattr(tray_module, "CREATE_INTERVAL_MS", 9999)
    tray._create_timer.setInterval(9999)

    snapshots = {device_id: Snapshot(device_id=device_id, level=80, online=True) for device_id in ids}
    tray.update_snapshots(snapshots)
    qapp.processEvents()        # 换血定时器（0ms）到点：注销旧图标 + 建第一枚

    assert len(tray._icons) == 1, "批量建完就不叫逐个注册了"
    assert tray._icon_ids == ids[-1:]

    # 剩下的按注册顺序补齐
    for _ in range(len(ids)):
        tray._create_next()
    assert tray._icon_ids == ids


def test_tray_registers_in_reverse_so_display_order_matches_panel(qapp, tmp_path, monkeypatch):
    """Windows 把新注册的图标插在最前面，所以要倒着注册，显示出来才是主界面顺序。"""
    from app.core.config import DeviceRegistry, SettingsStore
    from app.core.model import Snapshot
    from app.ui import tray as tray_module
    from app.ui.tray import TrayController

    created = _install_fake_tray(monkeypatch)
    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    tray = TrayController(registry, store)
    ids = [d.id for d in registry.enabled_devices()]
    assert len(ids) >= 3

    monkeypatch.setattr(tray_module, "CREATE_INTERVAL_MS", 9999)
    tray._create_timer.setInterval(9999)
    snapshots = {device_id: Snapshot(device_id=device_id, level=80, online=True) for device_id in ids}
    tray.update_snapshots(snapshots)
    qapp.processEvents()

    assert tray._create_queue == list(reversed(ids))[1:], "注册顺序必须是显示顺序的倒序"

    for _ in range(len(ids)):
        tray._create_next()

    # 注册先后 = ids 的倒序；内部列表仍按显示顺序
    assert tray._icon_ids == ids
    assert len(created) == len(ids)
    first_registered = created[0]
    assert tray._icons[0] is created[-1], "最后注册的那枚才是显示在最前面的"
    assert first_registered is created[0]


def test_tray_per_device_independent_icons(qapp, tmp_path, monkeypatch):
    """每台可见设备一枚独立图标，提示各对应自己的设备。"""
    from app.core.config import DeviceRegistry, SettingsStore
    from app.core.model import Snapshot
    from app.ui.tray import TrayController

    _install_fake_tray(monkeypatch)
    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    tray = TrayController(registry, store)

    ids = [d.id for d in registry.enabled_devices()]
    assert len(ids) >= 2
    snapshots = {
        device_id: Snapshot(device_id=device_id, level=80, charging=False, online=True)
        for device_id in ids
    }
    tray.update_snapshots(snapshots)
    _settle(qapp)

    assert len(tray._icons) == len(ids)
    assert tray._icon_ids == ids
    for icon, device_id in zip(tray._icons, tray._icon_ids):
        cfg = registry.get(device_id)
        assert cfg.display_name in icon.tooltips[-1]


def test_tray_hidden_device_gets_no_icon(qapp, tmp_path, monkeypatch):
    """被隐藏的设备不出现在托盘（图标与菜单列表都没有），主界面不受影响。"""
    from app.core.config import DeviceRegistry, SettingsStore
    from app.core.model import Snapshot
    from app.ui.tray import TrayController

    _install_fake_tray(monkeypatch)
    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    ids = [d.id for d in registry.enabled_devices()]
    assert len(ids) >= 2
    store.prefs.tray_hidden_devices = [ids[0]]
    tray = TrayController(registry, store)

    snapshots = {
        device_id: Snapshot(device_id=device_id, level=80, charging=False, online=True)
        for device_id in ids
    }
    tray.update_snapshots(snapshots)
    _settle(qapp)

    assert tray._icon_ids == ids[1:]
    assert ids[0] not in [d.id for d in tray._visible]


def test_tray_all_hidden_falls_back_to_placeholder(qapp, tmp_path, monkeypatch):
    """全部设备都被隐藏时回落占位图标，托盘入口不消失。"""
    from app.core.config import DeviceRegistry, SettingsStore
    from app.core.model import Snapshot
    from app.ui.tray import TrayController

    _install_fake_tray(monkeypatch)
    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    ids = [d.id for d in registry.enabled_devices()]
    store.prefs.tray_hidden_devices = list(ids)
    tray = TrayController(registry, store)

    snapshots = {
        device_id: Snapshot(device_id=device_id, level=80, charging=False, online=True)
        for device_id in ids
    }
    tray.update_snapshots(snapshots)
    _settle(qapp)

    assert len(tray._icons) == 1
    assert tray._icons[0].tooltips[-1] == "电量中枢 · 未检测到设备"
    assert tray._icon_ids == []
