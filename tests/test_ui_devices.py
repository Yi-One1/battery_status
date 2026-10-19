"""设备管理界面测试（问题3 的交互层）。

验证设置页的设备清单、新增对话框、以及面板收到"发现新设备"后的落盘行为。
全部在离屏模式下跑，不碰真实硬件。
"""
from __future__ import annotations

import pytest

from app.core.config import SOURCE_AUTO, SOURCE_USER, DeviceConfig, DeviceRegistry, SettingsStore
from app.core.discovery import describe_group
from app.core.hidapi_io import HidDeviceInfo
from app.ui.add_device import AddDeviceDialog, _CandidateRow
from app.ui.settings import SettingsPane

BUILTIN_ID = "logitech-gpx-superlight"


def hid(vid: int, pid: int, page: int, product: str, path: str, usage: int = 0x06, iface: int = 1):
    return HidDeviceInfo(
        path=path.encode(), vid=vid, pid=pid, product=product, manufacturer="",
        usage_page=page, usage=usage, interface=iface,
    )


RECEIVER = [
    hid(0x1234, 0x0001, 0x0001, "2.4G Wireless Receiver", "if0-kbd", iface=0),
    hid(0x1234, 0x0001, 0xFF00, "2.4G Wireless Receiver", "if1-vendor00"),
    hid(0x1234, 0x0001, 0xFF02, "2.4G Wireless Receiver", "if1-vendor02"),
]

NEWCOMER = [
    hid(0x0955, 0x7E11, 0xFF00, "Test Wheel Base", "wheel-vendor"),
]


@pytest.fixture()
def env(qapp, tmp_path):
    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    return registry, store


# --------------------------------------------------------------------------- #
# 设置页设备清单
# --------------------------------------------------------------------------- #
def test_settings_lists_every_enabled_device(env):
    registry, store = env
    pane = SettingsPane(registry, store)
    assert set(pane.device_widgets()) == {cfg.id for cfg in registry.enabled_devices()}


def test_settings_remove_builtin_device(env, tmp_path):
    registry, store = env
    pane = SettingsPane(registry, store)
    emitted = []
    pane.devices_changed.connect(lambda: emitted.append(True))

    assert pane.remove_device(BUILTIN_ID) is True
    assert registry.get(BUILTIN_ID) is None
    assert BUILTIN_ID not in pane.device_widgets()
    assert emitted == [True]
    # 墓碑落盘，重启后仍然是删掉的状态
    assert DeviceRegistry(user_dir=tmp_path).get(BUILTIN_ID) is None


def test_settings_remove_unknown_device_is_noop(env):
    registry, store = env
    pane = SettingsPane(registry, store)
    emitted = []
    pane.devices_changed.connect(lambda: emitted.append(True))
    assert pane.remove_device("does-not-exist") is False
    assert emitted == []


def test_settings_refresh_devices_picks_up_registry_change(env):
    registry, store = env
    pane = SettingsPane(registry, store)
    registry.add_device(DeviceConfig(id="late-device", type="mouse", display_name="后来的设备"))
    assert "late-device" not in pane.device_widgets()

    pane.refresh_devices()
    assert "late-device" in pane.device_widgets()


def test_settings_auto_flags_persist(env, tmp_path):
    registry, store = env
    pane = SettingsPane(registry, store)
    pane.auto_add_toggle.setChecked(False)
    pane.auto_hide_toggle.setChecked(False)
    pane.offline_picker.changed.emit("30")

    saved = SettingsStore(registry, path=tmp_path / "preferences.json")
    assert saved.prefs.auto_add is False
    assert saved.prefs.auto_hide_offline is False
    assert saved.prefs.offline_hide_minutes == 30


def test_settings_restore_row_hidden_until_something_removed(env):
    registry, store = env
    pane = SettingsPane(registry, store)
    # 面板本身没显示到屏幕上，只能用 isHidden() 读"是否被显式隐藏"
    assert pane._restore_box.isHidden() is True
    assert pane.restore_removed() == 0

    pane.remove_device(BUILTIN_ID)
    assert pane._restore_box.isHidden() is False
    assert "已移除" in pane._restore_label.text()


def test_settings_restore_brings_device_back(env, tmp_path):
    registry, store = env
    pane = SettingsPane(registry, store)
    pane.remove_device(BUILTIN_ID)
    assert pane.restore_removed() == 1

    assert registry.get(BUILTIN_ID) is not None
    assert BUILTIN_ID in pane.device_widgets()
    assert registry.removed_ids() == []
    assert DeviceRegistry(user_dir=tmp_path).get(BUILTIN_ID) is not None
    assert pane._restore_box.isHidden() is True


# --------------------------------------------------------------------------- #
# 新增设备对话框
# --------------------------------------------------------------------------- #
def test_dialog_lists_detected_candidates(env):
    registry, store = env
    dialog = AddDeviceDialog(registry, infos=RECEIVER)
    rows = dialog.findChildren(_CandidateRow)
    assert len(rows) == 1, "同一个 VID:PID 的多个接口应该只出现一行"
    assert rows[0].device.key == "1234:0001"


def test_dialog_shows_hint_when_nothing_detected(env):
    registry, store = env
    dialog = AddDeviceDialog(registry, infos=[])
    assert dialog.findChildren(_CandidateRow) == []


def test_dialog_add_discovered_writes_registry(env, tmp_path):
    registry, store = env
    dialog = AddDeviceDialog(registry, infos=NEWCOMER)
    added = []
    dialog.device_added.connect(lambda cfg: added.append(cfg))

    row = dialog.findChildren(_CandidateRow)[0]
    dialog._add_discovered(row.device)

    cfg = registry.get("auto-0955-7e11")
    assert cfg is not None
    assert cfg.source == SOURCE_AUTO
    assert added and added[0].id == "auto-0955-7e11"
    assert DeviceRegistry(user_dir=tmp_path).get("auto-0955-7e11") is not None


def test_dialog_manual_add(env):
    registry, store = env
    dialog = AddDeviceDialog(registry, infos=[])
    dialog.vid_edit.setText("046D")
    dialog.pid_edit.setText("C547")
    dialog.name_edit.setText("旧罗技接收器")
    dialog.type_picker.set_value("keyboard")

    dialog._add_manual()

    cfg = registry.get("auto-046d-c547")
    assert cfg is not None
    assert cfg.display_name == "旧罗技接收器"
    assert cfg.type == "keyboard"
    assert cfg.source == SOURCE_USER
    assert cfg.match == {"vid": 0x046D, "pid": 0xC547}


def test_dialog_manual_add_rejects_bad_hex(env):
    registry, store = env
    before = len(registry.devices)
    dialog = AddDeviceDialog(registry, infos=[])
    dialog.vid_edit.setText("zzzz")
    dialog.pid_edit.setText("01BB")

    dialog._add_manual()

    assert len(registry.devices) == before
    assert dialog.hint.isVisible() or dialog.hint.text(), "非法输入必须给出提示"
    assert "十六进制" in dialog.hint.text()


def test_dialog_manual_add_rejects_out_of_range(env):
    registry, store = env
    before = len(registry.devices)
    dialog = AddDeviceDialog(registry, infos=[])
    dialog.vid_edit.setText("10000")
    dialog.pid_edit.setText("01BB")
    dialog._add_manual()
    assert len(registry.devices) == before
    assert "范围" in dialog.hint.text()


# --------------------------------------------------------------------------- #
# 面板：自动新增后立即出卡片
# --------------------------------------------------------------------------- #
def test_panel_registers_discovered_once(qapp, env):
    from app.core.monitor import Monitor
    from app.ui.panel import Panel
    from app.ui.tray import TrayController

    registry, store = env
    monitor = Monitor(registry, store.prefs)
    tray = TrayController(registry, store)
    panel = Panel(registry, store, monitor, tray)
    try:
        device = describe_group(NEWCOMER)
        panel._on_discovered([device])

        assert registry.get("auto-0955-7e11") is not None
        assert "auto-0955-7e11" in panel._cards

        # 同一台设备重复上报不应该产生第二条配置
        panel._on_discovered([device])
        assert [d.id for d in registry.devices].count("auto-0955-7e11") == 1
    finally:
        panel.deleteLater()


def test_panel_ignores_tombstoned_device(qapp, env):
    from app.core.monitor import Monitor
    from app.ui.panel import Panel
    from app.ui.tray import TrayController

    registry, store = env
    monitor = Monitor(registry, store.prefs)
    tray = TrayController(registry, store)
    panel = Panel(registry, store, monitor, tray)
    try:
        device = describe_group(NEWCOMER)
        panel._on_discovered([device])
        registry.remove_device("auto-0955-7e11")

        panel._on_discovered([device])
        assert registry.get("auto-0955-7e11") is None, "用户删过的设备不该被自动加回来"
    finally:
        panel.deleteLater()


def test_panel_hides_long_offline_device(qapp, env):
    import time

    from app.core.model import Snapshot
    from app.core.monitor import Monitor
    from app.ui.panel import Panel
    from app.ui.tray import TrayController

    registry, store = env
    monitor = Monitor(registry, store.prefs)
    tray = TrayController(registry, store)
    panel = Panel(registry, store, monitor, tray)
    try:
        ids = {cfg.id for cfg in registry.enabled_devices()}
        assert set(panel._cards) == ids

        now = time.time()
        store.prefs.offline_hide_minutes = 10
        snapshots = {}
        for cfg in registry.enabled_devices():
            offline = cfg.id == BUILTIN_ID
            snapshots[cfg.id] = Snapshot(
                device_id=cfg.id,
                level=50,
                online=not offline,
                last_seen=now - (3600 if offline else 1),
                updated_at=now - 1,
            )
        panel.apply_snapshots(snapshots)

        assert BUILTIN_ID not in panel._cards, "断连超过阈值应该被收起"
        assert len(panel._cards) == len(ids) - 1

        # 重新连上后卡片要能自己回来
        snapshots[BUILTIN_ID] = Snapshot(device_id=BUILTIN_ID, level=60, online=True, last_seen=time.time())
        panel.apply_snapshots(snapshots)
        assert BUILTIN_ID in panel._cards
    finally:
        panel.deleteLater()
