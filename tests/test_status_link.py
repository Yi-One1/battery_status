"""链路状态（有线 / 2.4G）的测试。

问题4 的两件事：
  1. **有线时不许显示"休眠中"** —— 插着线时鼠标应答在 HID++ 索引 0xFF 上，
     接收器那一路是休眠的，原来遇到休眠直接 return，就把有线报成了休眠；
  2. **状态行要说出链路** —— 2.4G 就写「2.4G 连接」，插线就写「有线 · 充电中」。
"""
from __future__ import annotations

import pytest

from app.core import protocols
from app.core.model import LINK_WIRED, LINK_WIRELESS, Reading, Snapshot
from app.core.protocols import HidppDevice, HidppProbe, read_device


def snap(**kwargs) -> Snapshot:
    base = dict(device_id="logitech-gpx-superlight", online=True, level=62)
    base.update(kwargs)
    return Snapshot(**base)


# --------------------------------------------------------------------------- #
# 状态行
# --------------------------------------------------------------------------- #
def test_wireless_link_is_shown_when_idle():
    assert snap(link=LINK_WIRELESS).status_text() == "2.4G 连接"


def test_wired_link_is_shown_while_charging():
    """问题4 的正例：插着线时应该是「有线 · 充电中」，不是「休眠中」。"""
    assert snap(link=LINK_WIRED, charging=True).status_text() == "有线 · 充电中"


def test_wireless_link_is_shown_while_charging():
    assert snap(link=LINK_WIRELESS, charging=True).status_text() == "2.4G · 充电中"


def test_unknown_link_falls_back_to_plain_words():
    assert snap(link="").status_text() == "使用中"
    assert snap(link="", charging=True).status_text() == "充电中"


def test_link_does_not_hide_problems():
    """低电量、休眠、未连接这些更重要的信息不能被链路顶掉。"""
    assert snap(link=LINK_WIRELESS, level=8).status_text() == "电量偏低"
    assert snap(link=LINK_WIRELESS, sleeping=True).status_text() == "休眠中"
    assert snap(link=LINK_WIRELESS, online=False).status_text() == "未连接"
    assert snap(link=LINK_WIRELESS, level=None, charging=True).status_text() == "2.4G · 充电中"


def test_link_is_shown_when_level_is_unavailable():
    """飞智那类设备：硬件不输出电量，但"正连在 2.4G 上"是确凿的 —— 就写链路。"""
    assert snap(link=LINK_WIRELESS, level=None).status_text() == "2.4G 连接"
    assert snap(link=LINK_WIRED, level=None).status_text() == "有线 连接"


def test_no_level_and_no_link_still_says_unavailable():
    """连链路都判断不出来时，不许拿链路凑出一句话。"""
    assert snap(level=None).status_text() == "暂无电量"
    assert snap(level=None, link="").status_text() == "暂无电量"


def test_link_label_only_knows_the_two_real_cases():
    assert snap(link=LINK_WIRED).link_label == "有线"
    assert snap(link=LINK_WIRELESS).link_label == "2.4G"
    assert snap(link="bluetooth").link_label == ""


# --------------------------------------------------------------------------- #
# 有线 / 无线的探测与兜底
# --------------------------------------------------------------------------- #
class _Caps:
    input_length = 20


class _FakeHandle:
    def __init__(self, info) -> None:
        self.info = info
        self.is_open = True

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


class _FakeSession:
    def __init__(self, handle, message_size) -> None:
        pass

    def read_battery(self, index: int) -> Reading:
        return Reading(level=71 if index == 0xFF else 62, ok=True, source="hidpp", detail="fake")

    def device_name(self, index: int) -> str:
        return "PRO X Wireless"


def test_wired_device_is_tagged_as_wired(monkeypatch):
    monkeypatch.setattr(protocols, "device_caps", lambda path: _Caps())
    monkeypatch.setattr(protocols, "HidHandle", _FakeHandle)
    monkeypatch.setattr(protocols, "HidppSession", _FakeSession)
    monkeypatch.setattr(
        protocols,
        "hidpp_probe",
        lambda session, path, force=False: HidppProbe(
            devices=[HidppDevice(index=0xFF, name="PRO X Wireless", wired=True)]
        ),
    )

    cfg = _logitech_cfg()
    reading = protocols.read_logitech(cfg, _info())
    assert reading.ok is True
    assert reading.level == 71
    assert reading.link == LINK_WIRED


def test_receiver_device_is_tagged_as_wireless(monkeypatch):
    monkeypatch.setattr(protocols, "device_caps", lambda path: _Caps())
    monkeypatch.setattr(protocols, "HidHandle", _FakeHandle)
    monkeypatch.setattr(protocols, "HidppSession", _FakeSession)
    monkeypatch.setattr(
        protocols,
        "hidpp_probe",
        lambda session, path, force=False: HidppProbe(devices=[HidppDevice(index=1, name="PRO X Superlight")]),
    )

    reading = protocols.read_logitech(_logitech_cfg(), _info())
    assert reading.link == LINK_WIRELESS


def _info():
    from app.core.hidapi_io import HidDeviceInfo

    return HidDeviceInfo(
        path=b"\\\\?\\hid#vid_046d&pid_c547",
        vid=0x046D,
        pid=0xC547,
        product="USB Receiver",
        manufacturer="Logitech",
        usage_page=0xFF00,
        usage=0x01,
    )


def _logitech_cfg():
    from app.core.config import DeviceConfig

    return DeviceConfig(
        id="logitech-gpx-superlight",
        type="mouse",
        display_name="罗技 GPW2",
        match={"vid": 0x046D},
        protocol={"name": "logitech_hidpp", "device_name_contains": ["superlight"], "device_index": "auto"},
    )


# --------------------------------------------------------------------------- #
# 一路休眠不许挡住另一路
# --------------------------------------------------------------------------- #
def test_sleeping_receiver_does_not_mask_the_wired_path(monkeypatch):
    """核心回归：接收器那一路休眠时，要继续试有线接口，而不是直接返回「休眠中」。"""
    infos = [_info(), _info()]
    cfg = _logitech_cfg()
    monkeypatch.setattr(protocols, "candidate_infos", lambda infos, cfg: list(infos))

    calls: list[int] = []

    def fake_read(cfg, info):
        calls.append(id(info))
        if len(calls) == 1:
            return Reading.fail("设备休眠中", sleeping=True)
        return Reading(level=71, charging=True, source="hidpp", ok=True, link=LINK_WIRED)

    monkeypatch.setattr(protocols, "read_logitech", fake_read)
    reading = read_device(cfg, infos, set())

    assert len(calls) == 2
    assert reading.ok is True
    assert reading.link == LINK_WIRED
    assert reading.level == 71


def test_all_paths_sleeping_still_reports_sleeping(monkeypatch):
    infos = [_info(), _info()]
    cfg = _logitech_cfg()
    monkeypatch.setattr(protocols, "candidate_infos", lambda infos, cfg: list(infos))
    monkeypatch.setattr(
        protocols, "read_logitech", lambda cfg, info: Reading.fail("设备休眠中", sleeping=True)
    )

    reading = read_device(cfg, infos, set())
    assert reading.ok is False
    assert reading.sleeping is True


def test_first_working_path_wins_without_extra_probing(monkeypatch):
    """正常无线情况不能因为"有线优先"多开一轮句柄 —— 低占用是硬指标。"""
    infos = [_info(), _info(), _info()]
    cfg = _logitech_cfg()
    monkeypatch.setattr(protocols, "candidate_infos", lambda infos, cfg: list(infos))

    calls: list[int] = []

    def fake_read(cfg, info):
        calls.append(id(info))
        return Reading(level=62, source="hidpp", ok=True, link=LINK_WIRELESS)

    monkeypatch.setattr(protocols, "read_logitech", fake_read)
    read_device(cfg, infos, set())
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# 配置里的静态链路声明（接收器类设备）
# --------------------------------------------------------------------------- #
def _dongle_cfg(link: str = LINK_WIRELESS):
    from app.core.config import DeviceConfig

    return DeviceConfig(
        id="edifier-g3max",
        type="headset",
        display_name="漫步者 G3 MAX",
        match={"vid": 0x35BB},
        protocol={"name": "vendor_query", "command": "D0 {vid_be} 01 00", "link": link},
    )


def test_declared_link_fills_in_when_protocol_has_none(monkeypatch):
    """配的就是接收器本身，匹配上了就一定是 2.4G，配置里声明一句不算猜。"""
    monkeypatch.setattr(
        protocols, "_read_device", lambda cfg, infos, slots: Reading(level=48, ok=True, source="vendor-query")
    )
    assert read_device(_dongle_cfg(), [], set()).link == LINK_WIRELESS


def test_declared_link_does_not_override_a_known_link(monkeypatch):
    """HID++ 自己已经说了有线，配置里的 wireless 不能把它盖掉。"""
    monkeypatch.setattr(
        protocols, "_read_device", lambda cfg, infos, slots: Reading(level=71, ok=True, link=LINK_WIRED)
    )
    assert read_device(_dongle_cfg(), [], set()).link == LINK_WIRED


def test_dongle_configs_declare_wireless():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    data = json.loads((root / "app" / "config" / "devices.json").read_text(encoding="utf-8"))
    for device_id in ("mchose-g98pro", "edifier-g3max"):
        device = next(d for d in data["devices"] if d["id"] == device_id)
        assert device["protocol"]["link"] == "wireless"
    # 飞智改为基于端口动态判别（有线/2.4G 共享 USB 实例 ID，只能看端口）
    flydigi = next(d for d in data["devices"] if d["id"] == "flydigi-apex4")
    assert flydigi["protocol"]["link"]["mode"] == "port"
    assert flydigi["protocol"]["link"]["wireless_port"]
    mouse = next(d for d in data["devices"] if d["id"] == "logitech-gpx-superlight")
    # 鼠标的链路是运行时判定的，配置里不能预先声明
    assert "link" not in mouse["protocol"]


# --------------------------------------------------------------------------- #
# 端口判别链路（飞智这类共享 USB 实例 ID 的设备）
# --------------------------------------------------------------------------- #
def _flydigi_info():
    from app.core.hidapi_io import HidDeviceInfo

    return HidDeviceInfo(
        path=b"hid-fake-path",
        vid=0x045E,
        pid=0x028E,
        serial="FLYDIGI_DIREWOLF_4",
        product="Controller (Flydigi Direwolf 4)",
    )


def _flydigi_cfg():
    from app.core.config import DeviceConfig

    return DeviceConfig(
        id="flydigi-apex4",
        type="gamepad",
        display_name="飞智 冰原狼 4",
        match={"vid": 1118, "product_contains": ["flydigi"]},
        protocol={
            "name": "xinput",
            "slot": "auto",
            "link": {
                "mode": "port",
                "wireless_port": "Port_#0011.Hub_#0001",
                "wired_charging": True,
            },
        },
    )


def test_port_link_wireless(monkeypatch):
    """端口等于 wireless_port 时判为 2.4G。"""
    monkeypatch.setattr(
        protocols.xinput, "read_device", lambda slot, used: Reading(level=None, ok=True, source="xinput")
    )
    monkeypatch.setattr(
        protocols,
        "location_from_vid_pid_serial",
        lambda vid, pid, serial: "Port_#0011.Hub_#0001",
    )

    reading = read_device(_flydigi_cfg(), [_flydigi_info()], set())
    assert reading.ok is True
    assert reading.link == LINK_WIRELESS
    assert reading.charging is None


def test_port_link_wired(monkeypatch):
    """端口不等于 wireless_port 时判为有线；wired_charging 会置 charging=True。"""
    monkeypatch.setattr(
        protocols.xinput, "read_device", lambda slot, used: Reading(level=None, ok=True, source="xinput")
    )
    monkeypatch.setattr(
        protocols,
        "location_from_vid_pid_serial",
        lambda vid, pid, serial: "Port_#0005.Hub_#0001",
    )

    reading = read_device(_flydigi_cfg(), [_flydigi_info()], set())
    assert reading.ok is True
    assert reading.link == LINK_WIRED
    assert reading.charging is True


def test_port_link_no_candidate_keeps_default(monkeypatch):
    """没匹配到 HID 信息时无法判别，链路留空，后续不覆盖。"""
    monkeypatch.setattr(
        protocols.xinput, "read_device", lambda slot, used: Reading(level=None, ok=True, source="xinput")
    )

    reading = read_device(_flydigi_cfg(), [], set())
    assert reading.ok is True
    assert reading.link == ""


def test_port_link_device_not_ok_does_not_guess(monkeypatch):
    """设备未连接时绝不靠端口猜链路。"""
    monkeypatch.setattr(
        protocols.xinput, "read_device", lambda slot, used: Reading.fail("手柄未连接")
    )
    monkeypatch.setattr(
        protocols,
        "location_from_vid_pid_serial",
        lambda vid, pid, serial: "Port_#0011.Hub_#0001",
    )

    reading = read_device(_flydigi_cfg(), [_flydigi_info()], set())
    assert reading.ok is False
    assert reading.link == ""
