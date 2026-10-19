"""read_device 统一入口的链路/充电判断测试。

覆盖：xinput、vendor_query、logitech_hidpp、hid 四种协议在 read_device 出口处的
链路/充电状态是否一致、是否互不覆盖。
"""
from __future__ import annotations

import pytest

from app.core import protocols
from app.core.config import DeviceConfig
from app.core.hidapi_io import HidDeviceInfo
from app.core.model import LINK_WIRED, LINK_WIRELESS
from app.core.protocols import read_device


WIRELESS_PORT = "Port_#0011.Hub_#0001"
WIRED_PORT = "Port_#0005.Hub_#0001"


def _info(vid: int, pid: int, serial: str = "") -> HidDeviceInfo:
    return HidDeviceInfo(
        path=br"\\?\hid#vid_test",
        vid=vid,
        pid=pid,
        serial=serial,
        product="Test Device",
        manufacturer="Test",
        usage_page=0xFF02,
        usage=0x02,
    )


def _cfg(name: str, **proto) -> DeviceConfig:
    return DeviceConfig(
        id=f"test-{name}",
        type="mouse",
        display_name="Test",
        match={"vid": 0x1234},
        protocol={"name": name, **proto},
    )


@pytest.fixture
def patch_xinput(monkeypatch):
    def _patch():
        from app.core import xinput as xin
        monkeypatch.setattr(
            xin, "read_device",
            lambda slot, used: type("R", (), {
                "level": None, "charging": None, "ok": True, "source": "xinput",
                "detail": "", "sleeping": False, "detected": False, "offline": False, "link": "",
            })(),
        )
    return _patch


@pytest.fixture
def patch_vendor(monkeypatch):
    def _patch():
        from app.core import protocols as p
        monkeypatch.setattr(
            p, "read_vendor_query",
            lambda cfg, info: type("R", (), {
                "level": 62, "charging": None, "ok": True, "source": "vendor-query",
                "detail": "", "sleeping": False, "detected": False, "offline": False, "link": "",
            })(),
        )
    return _patch


@pytest.fixture
def patch_port(monkeypatch):
    def _patch(port: str | None):
        from app.core import protocols as p
        monkeypatch.setattr(
            p, "location_from_vid_pid_serial",
            lambda vid, pid, serial: port,
        )
    return _patch


def test_xinput_port_wireless(patch_xinput, patch_port):
    patch_xinput()
    patch_port(WIRELESS_PORT)
    cfg = _cfg("xinput", slot="auto", link={"mode": "port", "wireless_port": WIRELESS_PORT})
    reading = read_device(cfg, [_info(0x1234, 1, "S")], set())
    assert reading.ok is True
    assert reading.link == LINK_WIRELESS
    assert reading.charging is None


def test_xinput_port_wired_sets_charging(patch_xinput, patch_port):
    patch_xinput()
    patch_port(WIRED_PORT)
    cfg = _cfg("xinput", slot="auto", link={"mode": "port", "wireless_port": WIRELESS_PORT, "wired_charging": True})
    reading = read_device(cfg, [_info(0x1234, 1, "S")], set())
    assert reading.ok is True
    assert reading.link == LINK_WIRED
    assert reading.charging is True


def test_vendor_query_port_wireless(patch_vendor, patch_port):
    patch_vendor()
    patch_port(WIRELESS_PORT)
    cfg = _cfg("vendor_query", command="00", report_length=8, link={"mode": "port", "wireless_port": WIRELESS_PORT})
    reading = read_device(cfg, [_info(0x1234, 1, "S")], set())
    assert reading.ok is True
    assert reading.link == LINK_WIRELESS


def test_vendor_query_port_wired_sets_charging(patch_vendor, patch_port):
    patch_vendor()
    patch_port(WIRED_PORT)
    cfg = _cfg("vendor_query", command="00", report_length=8,
               link={"mode": "port", "wireless_port": WIRELESS_PORT, "wired_charging": True})
    reading = read_device(cfg, [_info(0x1234, 1, "S")], set())
    assert reading.ok is True
    assert reading.link == LINK_WIRED
    assert reading.charging is True


def test_static_link_string_still_works(patch_vendor, patch_port):
    """没有端口配置时，静态字符串声明继续生效。"""
    patch_vendor()
    patch_port(None)
    cfg = _cfg("vendor_query", command="00", report_length=8, link="wireless")
    reading = read_device(cfg, [_info(0x1234, 1, "S")], set())
    assert reading.ok is True
    assert reading.link == LINK_WIRELESS


def test_logitech_link_not_overwritten_by_port(monkeypatch, patch_port):
    """HID++ 自己已判定链路时，端口逻辑不应覆盖。"""
    patch_port(WIRED_PORT)
    from app.core import protocols as p

    class FakeReading:
        level = 80
        charging = False
        ok = True
        source = "hidpp"
        detail = ""
        sleeping = False
        detected = False
        offline = False
        link = LINK_WIRELESS

    monkeypatch.setattr(p, "read_logitech", lambda cfg, info: FakeReading())
    cfg = _cfg("logitech_hidpp", link={"mode": "port", "wireless_port": WIRELESS_PORT})
    reading = read_device(cfg, [_info(0x1234, 1, "S")], set())
    assert reading.link == LINK_WIRELESS
