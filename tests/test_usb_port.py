"""测试 USB 端口读取辅助函数。"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "app/core")
from app.core import usb_port


def test_location_returns_port_string(monkeypatch):
    """注册表正常命中时返回 LocationInformation 字符串。"""
    fake_key = MagicMock()
    fake_key.__enter__ = lambda self: self
    fake_key.__exit__ = lambda *args: None

    with patch("app.core.usb_port.winreg.OpenKey", return_value=fake_key) as open_key:
        with patch(
            "app.core.usb_port.winreg.QueryValueEx",
            return_value=("Port_#0011.Hub_#0001", 1),
        ):
            result = usb_port.location_from_vid_pid_serial(0x045E, 0x028E, "FLYDIGI_DIREWOLF_4")

    assert result == "Port_#0011.Hub_#0001"
    args, _ = open_key.call_args
    assert args == (usb_port.winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\USB\VID_045E&PID_028E\FLYDIGI_DIREWOLF_4")


def test_location_missing_key_returns_none(monkeypatch):
    """键不存在时返回 None，不抛异常。"""
    import winreg

    with monkeypatch.context() as m:
        m.setattr(
            winreg,
            "OpenKey",
            lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()),
        )
        assert usb_port.location_from_vid_pid_serial(0x045E, 0x028E, "X") is None


def test_location_empty_serial_returns_none():
    """空序列号直接返回 None，避免构造错误注册表路径。"""
    assert usb_port.location_from_vid_pid_serial(0x045E, 0x028E, "") is None
