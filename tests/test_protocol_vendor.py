"""厂商查询协议（vendor_query）测试。

用真实抓包帧做夹具，锁住这些事：
  1. 命令模板的字节序（漫步者的帧是 `D0 35 BB`，不是 `D0 BB 35`）
  2. 帧尾校验和（迈从的帧是前 19 字节求和）
  3. 应答解析只认自己的前缀 + 状态码，别把别的报文当成电量
  4. dongle 的回显帧必须被排掉 —— 否则电量位为 0，会显示成 0%
  5. 电量必须落在 0..100，否则宁可报错也不显示假数据
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import DeviceConfig
from app.core.hidapi_io import HidDeviceInfo
from app.core.protocols import (
    apply_checksum,
    build_vendor_command,
    parse_vendor_reply,
    read_vendor_query,
    render_hex_template,
)

ROOT = Path(__file__).resolve().parent.parent


def fake_info(vid: int = 0x35BB, pid: int = 0xA226) -> HidDeviceInfo:
    return HidDeviceInfo(
        path=b"\\\\?\\hid#vid_35bb&pid_a226",
        vid=vid,
        pid=pid,
        product="HECATE G3 MAX",
        manufacturer="Edifier",
        usage_page=0xFF02,
        usage=0x02,
    )


# 实测抓到的应答：D0 35 BB 0A 00 01 30 00 …，byte6 = 0x30 = 48
REAL_REPLY = bytes([0xD0, 0x35, 0xBB, 0x0A, 0x00, 0x01, 0x30, 0x00, 0x01, 0x02, 0x06, 0x01, 0x02, 0x03] + [0] * 50)


def test_vid_template_uses_high_byte_first():
    info = fake_info()
    assert render_hex_template("{vid_be}", info) == bytes([0x35, 0xBB])
    assert render_hex_template("{vid_le}", info) == bytes([0xBB, 0x35])
    assert render_hex_template("{vid}", info) == bytes([0x35, 0xBB])
    assert render_hex_template("{pid_be}", info) == bytes([0xA2, 0x26])


def test_command_is_padded_to_report_length():
    info = fake_info()
    command = build_vendor_command("D0 {vid_be} 01 00", info, 64)
    assert len(command) == 64
    assert command[:5] == bytes([0xD0, 0x35, 0xBB, 0x01, 0x00])
    assert command[5:] == b"\x00" * 59


def test_command_is_truncated_when_template_too_long():
    info = fake_info()
    command = build_vendor_command("D0 {vid_be} 01 00 FF FF", info, 5)
    assert command == bytes([0xD0, 0x35, 0xBB, 0x01, 0x00])


def test_empty_template_yields_empty_bytes():
    assert render_hex_template("", fake_info()) == b""
    assert build_vendor_command("", fake_info(), 0) == b""


def test_parse_real_reply_returns_48_percent():
    result = parse_vendor_reply(
        REAL_REPLY,
        prefix=bytes([0xD0, 0x35, 0xBB]),
        code_offset=3,
        code_value=0x0A,
        level_offset=6,
    )
    assert result is not None
    level, charging = result
    assert level == 48
    assert charging is None


def test_parse_rejects_foreign_prefix():
    """别的设备的报文（比如鼠标）不能被当成耳机电量。"""
    other = bytes([0xD0, 0x46, 0x0A, 0x0A, 0, 0, 60]) + bytes(20)
    assert (
        parse_vendor_reply(
            other, prefix=bytes([0xD0, 0x35, 0xBB]), code_offset=3, code_value=0x0A, level_offset=6
        )
        is None
    )


def test_parse_rejects_wrong_state_code():
    """ACK/写入类应答（如 0x0F）不应该被当成状态帧。"""
    assert (
        parse_vendor_reply(
            bytes([0xED, 0x35, 0xBB, 0x01, 0x0F]),
            prefix=bytes([0xED, 0x35, 0xBB]),
            code_offset=3,
            code_value=0x09,
            level_offset=6,
        )
        is None
    )


@pytest.mark.parametrize("bad_level", [101, 0x7F, 0xFF])
def test_parse_rejects_out_of_range_level(bad_level):
    frame = bytearray(REAL_REPLY)
    frame[6] = bad_level
    assert (
        parse_vendor_reply(
            bytes(frame), prefix=bytes([0xD0, 0x35, 0xBB]), code_offset=3, code_value=0x0A, level_offset=6
        )
        is None
    )


def test_parse_handles_short_and_empty_frames():
    assert parse_vendor_reply(b"", level_offset=6) is None
    assert parse_vendor_reply(bytes([0xD0, 0x35]), prefix=bytes([0xD0, 0x35, 0xBB]), level_offset=6) is None


def test_parse_charging_bit():
    frame = bytearray(REAL_REPLY)
    frame[5] = 0x02
    result = parse_vendor_reply(
        bytes(frame),
        prefix=bytes([0xD0, 0x35, 0xBB]),
        code_offset=3,
        code_value=0x0A,
        level_offset=6,
        charging_byte=5,
        charging_bit=1,
    )
    assert result == (48, True)


def test_edifier_config_wires_the_query_protocol():
    data = json.loads((ROOT / "app" / "config" / "devices.json").read_text(encoding="utf-8"))
    device = next(d for d in data["devices"] if d["id"] == "edifier-g3max")
    proto = device["protocol"]
    assert proto["name"] == "vendor_query"
    assert proto["command"] == "D0 {vid_be} 01 00"
    assert proto["reply_prefix"] == "D0 {vid_be}"
    assert proto["level_offset"] == 6
    assert proto["report_length"] == 64


def test_live_edifier_reads_a_sane_percentage():
    """有设备在场时真实读一次；没有设备就跳过（CI 上不会红）。"""
    from app.core.config import DeviceRegistry
    from app.core.hidapi_io import enumerate_devices
    from app.core.protocols import read_device

    registry = DeviceRegistry()
    cfg = next((d for d in registry.enabled_devices() if d.id == "edifier-g3max"), None)
    if cfg is None:
        pytest.skip("配置里没有漫步者")
    infos = enumerate_devices()
    if not any(i.vid == 0x35BB for i in infos):
        pytest.skip("设备未连接")

    reading = read_device(cfg, infos, set())
    if reading.ok:
        assert reading.level is not None and 0 <= reading.level <= 100
        assert reading.source == "vendor-query"
    elif reading.offline:
        # 关着 —— 正确结果就是"已关闭"，不许带着数值
        assert reading.level is None
    else:
        # 关机一段时间后 dongle 会完全静默，这种情况真机测试没法断言
        pytest.skip(f"耳机当前无应答：{reading.detail}")


# --------------------------------------------------------------------------- #
# 迈从 G98 PRO（258A:01BB）
#
# 实测帧（2026-10-20，真机；M HUB 同时显示 100%）：
#   写  13 4A 01 00 02 00 00 … 00 60     ← 尾字节 = 前 19 字节之和
#   收  13 4A 01 00 02 64 01 00 … 00 C5  ← byte5 = 0x64 = 100%
# --------------------------------------------------------------------------- #
def mchose_info() -> HidDeviceInfo:
    return HidDeviceInfo(
        path=b"\\\\?\\hid#vid_258a&pid_01bb&mi_01&col07",
        vid=0x258A,
        pid=0x01BB,
        product="2.4G Wireless Receiver",
        manufacturer="SINO WEALTH",
        usage_page=0xFF02,
        usage=0x02,
    )


MCHOSE_QUERY = bytes(
    [0x13, 0x4A, 0x01, 0x00, 0x02, 0x00] + [0x00] * 13 + [0x60]
)
MCHOSE_REPLY = bytes(
    [0x13, 0x4A, 0x01, 0x00, 0x02, 0x64, 0x01] + [0x00] * 12 + [0xC5]
)


def test_sum8_checksum_lands_on_the_last_byte():
    command = build_vendor_command("13 4A 01 00 02 00", mchose_info(), 20, "sum8")
    assert command == MCHOSE_QUERY
    assert len(command) == 20
    assert command[-1] == sum(command[:-1]) & 0xFF


def test_unknown_checksum_scheme_is_left_alone():
    """不认识的算法不许瞎猜 —— 算错了帧会被设备拒收，算"对了"反而可能写坏配置。"""
    payload = bytes([0x13, 0x4A, 0x01, 0x00, 0x02, 0x00, 0, 0])
    assert apply_checksum(payload, "") == payload
    assert apply_checksum(payload, "crc32") == payload


def test_xor8_checksum_is_supported():
    payload = bytes([0x10, 0x20, 0x30, 0x00])
    assert apply_checksum(payload, "xor8") == bytes([0x10, 0x20, 0x30, 0x00 ^ 0x10 ^ 0x20 ^ 0x30])


def test_mchose_reply_reads_100_percent():
    result = parse_vendor_reply(
        MCHOSE_REPLY, prefix=bytes([0x13, 0x4A, 0x01, 0x00, 0x02]), level_offset=5
    )
    assert result == (100, None)


def test_mchose_reply_rejects_a_different_sub_type():
    other = bytearray(MCHOSE_REPLY)
    other[4] = 0x04  # 按键事件那一类
    assert (
        parse_vendor_reply(bytes(other), prefix=bytes([0x13, 0x4A, 0x01, 0x00, 0x02]), level_offset=5)
        is None
    )


# --------------------------------------------------------------------------- #
# 主动查询的交互过程：回显必须排掉
# --------------------------------------------------------------------------- #
class _FakeCaps:
    def __init__(self, length: int = 20) -> None:
        self.input_length = length
        self.output_length = length
        self.feature_length = 0


class _FakeHandle:
    """按顺序吐预告好的输入帧；记录写出去的内容。"""

    def __init__(self, frames: list[bytes]) -> None:
        self._frames = list(frames)
        self.written: list[bytes] = []

    def __enter__(self) -> "_FakeHandle":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    @property
    def is_open(self) -> bool:
        return True

    def write(self, payload: bytes) -> int:
        self.written.append(bytes(payload))
        return len(payload)

    def read(self, size: int = 64, timeout_ms: int = 150):
        return self._frames.pop(0) if self._frames else None

    def drain(self, *args, **kwargs) -> list:
        return []

    def close(self) -> None:
        return None


def _mchose_cfg() -> DeviceConfig:
    return DeviceConfig(
        id="mchose-g98pro",
        type="keyboard",
        display_name="迈从 G98 PRO",
        match={"vid": 0x258A, "pid": 0x01BB},
        protocol={
            "name": "vendor_query",
            "command": "13 4A 01 00 02 00",
            "report_length": 20,
            "checksum": "sum8",
            "reply_prefix": "13 4A 01 00 02",
            "level_offset": 5,
            "timeout_ms": 200,
            "retries": 0,
        },
    )


def _patch_query(monkeypatch, frames: list[bytes], length: int = 20) -> _FakeHandle:
    handle = _FakeHandle(frames)
    monkeypatch.setattr("app.core.protocols.HidHandle", lambda info: handle)
    monkeypatch.setattr("app.core.protocols.device_caps", lambda path: _FakeCaps(length))
    return handle


def test_query_writes_the_checksummed_frame(monkeypatch):
    handle = _patch_query(monkeypatch, [MCHOSE_REPLY])
    reading = read_vendor_query(_mchose_cfg(), mchose_info())

    assert reading.ok and reading.level == 100
    assert reading.source == "vendor-query"
    assert handle.written == [MCHOSE_QUERY]


def test_echoed_command_is_not_mistaken_for_0_percent(monkeypatch):
    """dongle 会把不认识的命令原样回显；回显里电量位是 0，绝不能被当成 0%。"""
    _patch_query(monkeypatch, [MCHOSE_QUERY, MCHOSE_REPLY])
    reading = read_vendor_query(_mchose_cfg(), mchose_info())
    assert reading.ok and reading.level == 100


def test_only_echo_means_no_reading(monkeypatch):
    _patch_query(monkeypatch, [MCHOSE_QUERY, MCHOSE_QUERY])
    reading = read_vendor_query(_mchose_cfg(), mchose_info())
    assert not reading.ok
    assert reading.level is None
    # 没应答只是"这轮不知道"：不声称设备在、也不声称休眠，交给去抖判定
    assert reading.detected is False
    assert reading.sleeping is False
    assert reading.offline is False


def test_mchose_config_wires_the_query_protocol():
    data = json.loads((ROOT / "app" / "config" / "devices.json").read_text(encoding="utf-8"))
    device = next(d for d in data["devices"] if d["id"] == "mchose-g98pro")
    proto = device["protocol"]
    assert proto["name"] == "vendor_query"
    assert proto["command"] == "13 4A 01 00 02 00"
    assert proto["checksum"] == "sum8"
    assert proto["report_length"] == 20
    assert proto["reply_prefix"] == "13 4A 01 00 02"
    assert proto["level_offset"] == 5


def test_live_mchose_reads_a_sane_percentage():
    """有设备在场时真实读一次；没有设备就跳过（CI 上不会红）。"""
    from app.core.config import DeviceRegistry
    from app.core.hidapi_io import enumerate_devices
    from app.core.protocols import read_device

    registry = DeviceRegistry()
    cfg = next((d for d in registry.enabled_devices() if d.id == "mchose-g98pro"), None)
    if cfg is None:
        pytest.skip("配置里没有迈从")
    infos = enumerate_devices()
    if not any(i.vid == 0x258A for i in infos):
        pytest.skip("设备未连接")

    reading = read_device(cfg, infos, set())
    assert reading.ok, f"读取失败：{reading.detail}"
    assert reading.level is not None and 0 <= reading.level <= 100
    assert reading.source == "vendor-query"


# --------------------------------------------------------------------------- #
# 漫步者关机：不能把「关机」当成「电量 0%」
#
# 实测（2026-10-20）：
#   开机  D0 35 BB 0A 00 01 30 00   byte5=0x01、byte6=0x30=48%
#   关机  D0 35 BB 0A 00 00 00 00   byte5=0x00、byte6=0x00
# --------------------------------------------------------------------------- #
EDIFIER_ON = bytes([0xD0, 0x35, 0xBB, 0x0A, 0x00, 0x01, 0x30] + [0] * 57)
EDIFIER_OFF = bytes([0xD0, 0x35, 0xBB, 0x0A, 0x00, 0x00, 0x00] + [0] * 57)


def _edifier_cfg(presence: bool = True) -> DeviceConfig:
    proto = {
        "name": "vendor_query",
        "command": "D0 {vid_be} 01 00",
        "report_length": 64,
        "reply_prefix": "D0 {vid_be}",
        "reply_code_offset": 3,
        "reply_code": 10,
        "level_offset": 6,
        "timeout_ms": 200,
        "retries": 0,
    }
    if presence:
        proto["presence"] = {"offset": 5, "value": 1}
    return DeviceConfig(
        id="edifier-g3max",
        type="headset",
        display_name="漫步者 G3 MAX",
        match={"vid": 0x35BB},
        protocol=proto,
    )


def _edifier_info() -> HidDeviceInfo:
    return HidDeviceInfo(
        path=b"\\\\?\\hid#vid_35bb&pid_a226",
        vid=0x35BB,
        pid=0xA226,
        product="HECATE G3 MAX无线版",
        manufacturer="Edifier",
        usage_page=0xFF02,
        usage=0x02,
    )


def test_edifier_off_reply_is_offline_not_zero_percent(monkeypatch):
    """问题5 的核心：关机帧 (0A 00 00 00) 必须判成已关闭，而不是 0% 电量偏低。"""
    _patch_query(monkeypatch, [EDIFIER_OFF], length=64)
    reading = read_vendor_query(_edifier_cfg(), _edifier_info())

    assert reading.offline is True
    assert reading.ok is False
    assert reading.level is None
    assert "已关闭" in reading.detail


def test_edifier_on_reply_reads_48_percent(monkeypatch):
    _patch_query(monkeypatch, [EDIFIER_ON], length=64)
    reading = read_vendor_query(_edifier_cfg(), _edifier_info())

    assert reading.ok is True
    assert reading.offline is False
    assert reading.level == 48


def test_zero_level_with_the_presence_byte_still_reads_zero(monkeypatch):
    """电量真的是 0、但状态字节说"在用"：照样报 0%，不许吞掉。"""
    frame = bytearray(EDIFIER_OFF)
    frame[5] = 0x01
    _patch_query(monkeypatch, [bytes(frame)], length=64)
    reading = read_vendor_query(_edifier_cfg(), _edifier_info())

    assert reading.ok is True
    assert reading.level == 0


def test_nonzero_level_ignores_the_presence_byte(monkeypatch):
    """电量非 0 时不信那个字节 —— 它到底是"已连接"还是"充电中"还没验证过。"""
    frame = bytearray(EDIFIER_ON)
    frame[5] = 0x00
    _patch_query(monkeypatch, [bytes(frame)], length=64)
    reading = read_vendor_query(_edifier_cfg(), _edifier_info())

    assert reading.ok is True
    assert reading.level == 48


def test_without_presence_rule_zero_is_just_zero(monkeypatch):
    """没配 presence 的设备保持原样：0 就是 0，不做额外推断。"""
    _patch_query(monkeypatch, [EDIFIER_OFF], length=64)
    reading = read_vendor_query(_edifier_cfg(presence=False), _edifier_info())

    assert reading.ok is True
    assert reading.level == 0


def test_edifier_config_declares_presence_rule():
    data = json.loads((ROOT / "app" / "config" / "devices.json").read_text(encoding="utf-8"))
    device = next(d for d in data["devices"] if d["id"] == "edifier-g3max")
    assert device["protocol"]["presence"] == {"offset": 5, "value": 1}
