"""厂商查询协议（vendor_query）测试。

用真实抓包帧做夹具，锁住三件事：
  1. 命令模板的字节序（漫步者的帧是 `D0 35 BB`，不是 `D0 BB 35`）
  2. 应答解析只认自己的前缀 + 状态码，别把别的报文当成电量
  3. 电量必须落在 0..100，否则宁可报错也不显示假数据
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.hidapi_io import HidDeviceInfo
from app.core.protocols import build_vendor_command, parse_vendor_reply, render_hex_template

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
    assert reading.ok, f"读取失败：{reading.detail}"
    assert reading.level is not None and 0 <= reading.level <= 100
    assert reading.source == "vendor-query"
