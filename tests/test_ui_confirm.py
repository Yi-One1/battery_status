"""移除设备的二次确认（问题6）。

移除会把卡片和托盘图标一起拿掉，误点一次不该就没了。这里验证两件事：
  * 确认对话框自己的行为（标题、按钮、回车确认、Esc 取消）；
  * 设置页的「移除」按钮**必须**先问一句，取消就什么都不做。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QDialog

from app.core.config import DeviceRegistry, SettingsStore
from app.ui.confirm import ConfirmDialog
from app.ui.glass import PillButton
from app.ui.settings import SettingsPane

BUILTIN_ID = "logitech-gpx-superlight"


@pytest.fixture()
def env(qapp, tmp_path):
    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    return registry, store


# --------------------------------------------------------------------------- #
# 对话框本身
# --------------------------------------------------------------------------- #
def test_dialog_shows_title_and_both_choices(env):
    dialog = ConfirmDialog(
        None,
        title="移除设备",
        message="确定要移除「罗技 GPW2」吗？",
        detail="移除后可以恢复。",
        confirm_text="移除",
        tone="danger",
    )
    buttons = {b.text() for b in dialog.findChildren(PillButton)}
    assert dialog.windowTitle() == "移除设备"
    assert {"移除", "取消"} <= buttons
    assert dialog.confirm_button.tone == "danger"


def test_enter_accepts(env):
    dialog = ConfirmDialog(None, title="t", message="m")
    dialog.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    )
    assert dialog.result() == int(QDialog.DialogCode.Accepted)


def test_escape_cancels(env):
    dialog = ConfirmDialog(None, title="t", message="m")
    dialog.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    )
    assert dialog.result() == int(QDialog.DialogCode.Rejected)


def test_ask_maps_accept_to_true(env, monkeypatch):
    monkeypatch.setattr(
        ConfirmDialog, "exec", lambda self: QDialog.DialogCode.Accepted
    )
    assert ConfirmDialog.ask(None, title="t", message="m") is True

    monkeypatch.setattr(
        ConfirmDialog, "exec", lambda self: QDialog.DialogCode.Rejected
    )
    assert ConfirmDialog.ask(None, title="t", message="m") is False


# --------------------------------------------------------------------------- #
# 设置页：按钮必须先问
# --------------------------------------------------------------------------- #
class _FakeConfirm:
    answer = False
    calls: list = []

    @classmethod
    def ask(cls, parent, **kwargs):
        cls.calls.append(kwargs)
        return cls.answer


@pytest.fixture()
def fake_confirm(monkeypatch):
    _FakeConfirm.calls = []
    _FakeConfirm.answer = False
    monkeypatch.setattr("app.ui.settings.ConfirmDialog", _FakeConfirm)
    return _FakeConfirm


def test_remove_button_asks_before_removing(env, fake_confirm):
    registry, store = env
    pane = SettingsPane(registry, store)

    button = next(b for b in pane.findChildren(PillButton) if b.text() == "移除")
    button.click()

    assert fake_confirm.calls, "点「移除」应该先弹确认"
    assert fake_confirm.calls[0]["title"] == "移除设备"
    assert "移除" in fake_confirm.calls[0]["confirm_text"]
    # 用户点了取消 —— 设备必须还在
    assert registry.get(BUILTIN_ID) is not None


def test_cancelling_keeps_every_device(env, fake_confirm):
    registry, store = env
    pane = SettingsPane(registry, store)
    before = {cfg.id for cfg in registry.enabled_devices()}

    pane._confirm_remove(BUILTIN_ID)

    assert {cfg.id for cfg in registry.enabled_devices()} == before
    assert BUILTIN_ID in pane.device_widgets()


def test_confirming_does_remove(env, fake_confirm):
    registry, store = env
    pane = SettingsPane(registry, store)
    fake_confirm.answer = True
    emitted = []
    pane.devices_changed.connect(lambda: emitted.append(True))

    pane._confirm_remove(BUILTIN_ID)

    assert registry.get(BUILTIN_ID) is None
    assert BUILTIN_ID not in pane.device_widgets()
    assert emitted == [True]


def test_confirm_dialog_message_names_the_device(env, fake_confirm):
    registry, store = env
    pane = SettingsPane(registry, store)
    pane._confirm_remove(BUILTIN_ID)

    message = fake_confirm.calls[0]["message"]
    assert "罗技 GPW2" in message
