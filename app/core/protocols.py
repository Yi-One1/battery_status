"""读数协议层。

统一返回 Reading，监测线程与 UI 不需要知道设备怎么说话：
  * logitech_hidpp —— HID++ 2.0（Lightspeed / Unifying 接收器，也兼容有线模式）
  * hid            —— 标准 HID Battery Strength，回退到配置驱动的厂商报文解析
  * xinput         —— Xbox 兼容手柄的四档电量（Windows 原生 API）
  * demo           —— 演示数据

实测要点（罗技 GPW2 + C547 接收器）：
  * 20 字节接口必须用长报文 0x11，7 字节接口用短报文 0x10，混用会被系统直接拒绝
  * 新固件只有 0x1004 UNIFIED_BATTERY（feature index 6），没有 0x1000/0x1001
  * fn1 getStatus 返回 [电量%, 电量状态, 充电类型, 外部供电]，充电类型 0 = 放电
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Literal, Optional

from . import xinput
from .config import DeviceConfig, DeviceRegistry
from .hidapi_io import HidDeviceInfo, HidHandle, sort_preferred
from .hidnative import device_caps, read_battery_strength
from .model import Reading, LINK_WIRED, LINK_WIRELESS
from .usb_port import location_from_vid_pid_serial

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 罗技 HID++ 2.0
# --------------------------------------------------------------------------- #
HIDPP_SHORT_REPORT_ID = 0x10
HIDPP_LONG_REPORT_ID = 0x11
SHORT_MESSAGE_SIZE = 7
LONG_MESSAGE_SIZE = 20

FEATURE_ROOT = 0x0000
FEATURE_FW_VERSION = 0x0003
FEATURE_DEVICE_NAME = 0x0005
FEATURE_BATTERY_STATUS = 0x1000
FEATURE_BATTERY_VOLTAGE = 0x1001
FEATURE_UNIFIED_BATTERY = 0x1004

# 锂电池电压 -> 百分比（罗技 dongle 常用的估算曲线，线性插值）
_VOLTAGE_TABLE: tuple[tuple[int, int], ...] = (
    (4186, 100), (4067, 90), (3989, 80), (3922, 70), (3859, 60),
    (3811, 50), (3778, 40), (3751, 30), (3723, 20), (3671, 10),
    (3646, 5), (3579, 2), (3503, 0),
)


def voltage_to_percent(millivolts: int) -> int:
    if millivolts >= _VOLTAGE_TABLE[0][0]:
        return 100
    if millivolts <= _VOLTAGE_TABLE[-1][0]:
        return 0
    for (hi_mv, hi_pct), (lo_mv, lo_pct) in zip(_VOLTAGE_TABLE, _VOLTAGE_TABLE[1:]):
        if lo_mv <= millivolts <= hi_mv:
            span = hi_mv - lo_mv
            ratio = (millivolts - lo_mv) / span if span else 0.0
            return int(round(lo_pct + ratio * (hi_pct - lo_pct)))
    return 0


class HidppError(Exception):
    """设备返回错误应答。code=0x08 表示该索引上没有配对设备。"""

    def __init__(self, code: int) -> None:
        super().__init__(f"HID++ error 0x{code:02X}")
        self.code = code


class HidppTimeout(Exception):
    """请求已发出但无应答 —— 设备休眠或未唤醒。"""


class HidppSession:
    """一条 HID++ 通道。message_size 由接口的输入报告长度决定。"""

    def __init__(self, handle: HidHandle, message_size: int = SHORT_MESSAGE_SIZE) -> None:
        self.handle = handle
        self.message_size = LONG_MESSAGE_SIZE if message_size >= LONG_MESSAGE_SIZE else SHORT_MESSAGE_SIZE
        self._sw_id = 0
        self._features: dict[tuple[int, int], int] = {}

    def _next_sw(self) -> int:
        self._sw_id = self._sw_id % 14 + 1
        return self._sw_id

    def request(
        self,
        device_index: int,
        feature_index: int,
        function: int,
        params: bytes = b"",
        *,
        timeout_ms: int = 260,
    ) -> bytes:
        sw_id = self._next_sw()
        report_id = HIDPP_LONG_REPORT_ID if self.message_size == LONG_MESSAGE_SIZE else HIDPP_SHORT_REPORT_ID
        frame = bytearray([report_id, device_index, feature_index, (function << 4) | sw_id])
        frame.extend(params)
        if len(frame) < self.message_size:
            frame.extend(b"\x00" * (self.message_size - len(frame)))
        if self.handle.write(bytes(frame[: self.message_size])) < 0:
            raise HidppTimeout("写入被拒绝（接口可能被其他程序独占）")

        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            data = self.handle.read(self.message_size, 40)
            if not data or len(data) < 7:
                continue
            if data[1] != device_index or (data[3] & 0x0F) != sw_id:
                continue
            if data[2] & 0x80:
                raise HidppError(data[4] if len(data) > 4 else 0xFF)
            if data[2] == feature_index and (data[3] >> 4) == function:
                return bytes(data)
        raise HidppTimeout("无应答")

    def get_feature(self, device_index: int, feature_id: int) -> int:
        key = (device_index, feature_id)
        if key in self._features:
            return self._features[key]
        try:
            reply = self.request(device_index, FEATURE_ROOT, 0x00, bytes([feature_id >> 8, feature_id & 0xFF]))
            index = reply[4] if len(reply) > 4 else 0
        except HidppError:
            index = 0
        self._features[key] = index
        return index

    def device_name(self, device_index: int) -> str:
        index = self.get_feature(device_index, FEATURE_DEVICE_NAME)
        if not index:
            return ""
        try:
            # 0x0005 fn0 返回分片数，fn1 返回第 index 片；取首片足够区分设备型号
            self.request(device_index, index, 0x00)
            chunk = self.request(device_index, index, 0x01, bytes([0]))
            return chunk[4:20].split(b"\x00")[0].decode("utf-8", "ignore").strip()
        except (HidppError, HidppTimeout):
            return ""

    def state_of(self, device_index: int) -> Literal["present", "absent", "asleep"]:
        try:
            self.request(device_index, FEATURE_ROOT, 0x00, bytes([0x00, FEATURE_FW_VERSION]), timeout_ms=220)
            return "present"
        except HidppError as exc:
            return "absent" if exc.code == 0x08 else "present"
        except HidppTimeout:
            return "asleep"

    def read_battery(self, device_index: int) -> Reading:
        # ① UNIFIED_BATTERY（新固件首选）
        index = self.get_feature(device_index, FEATURE_UNIFIED_BATTERY)
        if index:
            try:
                reply = self.request(device_index, index, 0x01)
                if len(reply) >= 7 and reply[4] <= 100:
                    level = int(reply[4])
                    charging_type = reply[6] if len(reply) > 6 else 0
                    external = reply[7] if len(reply) > 7 else 0
                    charging = charging_type != 0 or external not in (0, 0xFF)
                    return Reading(
                        level=level,
                        charging=charging,
                        source="hidpp",
                        ok=True,
                        detail=f"HID++ 0x1004 · 状态 0x{reply[5]:02X} 充电类型 {charging_type}",
                    )
            except (HidppError, HidppTimeout):
                pass

        # ② BATTERY_STATUS（老固件）
        index = self.get_feature(device_index, FEATURE_BATTERY_STATUS)
        if index:
            try:
                reply = self.request(device_index, index, 0x00)
                if len(reply) >= 7 and reply[4] <= 100:
                    status = reply[6]
                    return Reading(
                        level=int(reply[4]),
                        charging=bool(status & 0x06),
                        source="hidpp",
                        ok=True,
                        detail=f"HID++ 0x1000 · 状态 0x{status:02X}",
                    )
            except (HidppError, HidppTimeout):
                pass

        # ③ BATTERY_VOLTAGE（毫伏换算）
        index = self.get_feature(device_index, FEATURE_BATTERY_VOLTAGE)
        if index:
            try:
                reply = self.request(device_index, index, 0x00)
                if len(reply) >= 7:
                    millivolts = (reply[4] << 8) | reply[5]
                    if 2500 <= millivolts <= 5000:
                        return Reading(
                            level=voltage_to_percent(millivolts),
                            charging=bool(reply[6] & 0x80),
                            source="hidpp",
                            ok=True,
                            detail=f"HID++ 0x1001 · {millivolts} mV",
                        )
            except (HidppError, HidppTimeout):
                pass
        return Reading.fail("HID++ 设备未返回电量")


@dataclass
class HidppDevice:
    index: int
    name: str
    wired: bool = False


@dataclass
class HidppProbe:
    devices: list[HidppDevice]
    asleep: bool = False


# 接收器发现结果缓存：path -> (时间戳, 结果)，避免每轮都做全索引试探
_HIDPP_CACHE: dict[str, tuple[float, HidppProbe]] = {}
_HIDPP_CACHE_TTL = 300.0


def _probe_wired(session: HidppSession) -> Optional[HidppDevice]:
    """有线模式：HID++ 通道本身就是设备，索引固定 0xFF。

    只在 1~6 号索引都没找到设备时才试。必须把超时也兜住 ——
    get_feature 只捕获 HidppError，HidppTimeout 会直接抛出去，
    不接的话"鼠标休眠"就会变成"读取异常"。
    """
    try:
        unified = session.get_feature(0xFF, FEATURE_UNIFIED_BATTERY)
        status = 0 if unified else session.get_feature(0xFF, FEATURE_BATTERY_STATUS)
    except (HidppError, HidppTimeout):
        return None
    if not (unified or status):
        return None
    return HidppDevice(index=0xFF, name=session.device_name(0xFF), wired=True)


def hidpp_probe(session: HidppSession, path: bytes, force: bool = False) -> HidppProbe:
    key = path.decode("utf-8", "ignore")
    now = time.monotonic()
    cached = _HIDPP_CACHE.get(key)
    if cached and not force and now - cached[0] < _HIDPP_CACHE_TTL:
        return cached[1]

    devices: list[HidppDevice] = []
    asleep = False
    for index in (1, 2, 3, 4, 5, 6):
        state = session.state_of(index)
        if state == "asleep":
            asleep = True
        elif state == "present":
            devices.append(HidppDevice(index=index, name=session.device_name(index)))

    if not devices:
        wired = _probe_wired(session)
        if wired is not None:
            devices.append(wired)

    probe = HidppProbe(devices=devices, asleep=asleep and not devices)
    if devices:
        _HIDPP_CACHE[key] = (now, probe)
    else:
        _HIDPP_CACHE.pop(key, None)
    return probe


def read_logitech(cfg: DeviceConfig, info: HidDeviceInfo) -> Reading:
    proto = cfg.protocol
    keywords = [str(x).lower() for x in (proto.get("device_name_contains") or [])]
    forced_index = proto.get("device_index")

    caps = device_caps(info.path)
    message_size = LONG_MESSAGE_SIZE if caps and caps.input_length >= LONG_MESSAGE_SIZE else SHORT_MESSAGE_SIZE

    with HidHandle(info) as handle:
        if not handle.is_open:
            return Reading.fail("接口被占用或设备已拔出（可能被 G HUB 独占）")
        session = HidppSession(handle, message_size)
        probe = hidpp_probe(session, info.path)
        if not probe.devices:
            if probe.asleep:
                return Reading.fail("设备休眠中，动一下鼠标即可刷新", sleeping=True)
            return Reading(detail="接收器上未发现配对设备", detected=True)

        target: Optional[HidppDevice] = None
        if isinstance(forced_index, int):
            target = next((d for d in probe.devices if d.index == forced_index), None)
        if target is None and keywords:
            target = next((d for d in probe.devices if any(k in d.name.lower() for k in keywords)), None)
        if target is None and len(probe.devices) == 1:
            target = probe.devices[0]
        if target is None:
            names = " / ".join(d.name or f"idx{d.index}" for d in probe.devices)
            return Reading.fail(f"接收器带多台设备：{names}")

        reading = session.read_battery(target.index)
        if not reading.ok:
            _HIDPP_CACHE.pop(info.path.decode("utf-8", "ignore"), None)
        # 索引 0xFF 是有线模式（HID++ 通道本身就是设备），1~6 是接收器上的无线设备。
        # 但部分新型号（如 GPW2 固件）插线后仍以索引 1 出现在有线 HID 接口上，
        # 因此再用 configured wired_pid 做一次兜底判别。
        raw_wired_pid = proto.get("wired_pid")
        wired_pids: set[int] = set()
        if isinstance(raw_wired_pid, (int, str)):
            wired_pids = {int(raw_wired_pid, 0) if isinstance(raw_wired_pid, str) else int(raw_wired_pid)}
        elif isinstance(raw_wired_pid, list):
            wired_pids = {int(p, 0) if isinstance(p, str) else int(p) for p in raw_wired_pid}
        if info.pid in wired_pids:
            reading.link = LINK_WIRED
        else:
            reading.link = LINK_WIRED if target.wired else LINK_WIRELESS
        if target.name:
            reading.detail = f"{target.name} · {reading.detail}" if reading.ok else reading.detail
        return reading


# --------------------------------------------------------------------------- #
# 通用 HID 设备（标准电量页 / 厂商报文）
# --------------------------------------------------------------------------- #
def _match_rule(data: bytes, prefix: str, mask: str) -> bool:
    try:
        prefix_bytes = bytes.fromhex(prefix) if prefix else b""
        mask_bytes = bytes.fromhex(mask) if mask else b""
    except ValueError:
        return False
    if not prefix_bytes:
        return True
    if len(data) < len(prefix_bytes):
        return False
    for index, expected in enumerate(prefix_bytes):
        mask_byte = mask_bytes[index] if index < len(mask_bytes) else 0xFF
        if (data[index] & mask_byte) != (expected & mask_byte):
            return False
    return True


def _extract_level(data: bytes, rule: dict[str, Any]) -> Optional[int]:
    offset = int(rule.get("offset", -1))
    if offset < 0 or offset >= len(data):
        return None
    raw = data[offset]
    bit = rule.get("bit")
    if isinstance(bit, int) and 0 <= bit <= 7:
        raw = (raw >> bit) & 0x01
    value = raw * float(rule.get("scale", 1) or 1)
    if rule.get("invert"):
        value = 100 - value
    return max(0, min(100, int(round(value))))


def _extract_charging(data: bytes, rule: dict[str, Any]) -> Optional[bool]:
    byte_index = rule.get("charging_byte")
    bit_index = rule.get("charging_bit", 0)
    if not isinstance(byte_index, int) or byte_index < 0 or byte_index >= len(data):
        return None
    if not isinstance(bit_index, int):
        return None
    return bool((data[byte_index] >> bit_index) & 0x01)


def read_generic_hid(cfg: DeviceConfig, info: HidDeviceInfo) -> Reading:
    proto = cfg.protocol
    rule = proto.get("parse") or {}
    init_packets = [str(p) for p in (proto.get("init") or [])]

    with HidHandle(info) as handle:
        if not handle.is_open:
            return Reading.fail("设备被占用或已拔出")

        # ① 标准电量页
        for _ in range(3):
            report = handle.read(64, 120)
            if not report:
                continue
            level = read_battery_strength(info.path, report)
            if level is not None:
                return Reading(
                    level=level,
                    charging=None,
                    source="hid-battery",
                    ok=True,
                    detail=f"HID 电量页 · {len(report)}B 报文",
                )

        if not rule:
            return Reading(
                detail="该 dongle 不暴露标准电量页，需在 devices.json 配置厂商报文规则（见 README 的接入流程）",
                detected=True,
            )

        # ② 厂商私有报文
        handle.drain()
        for packet in init_packets:
            try:
                handle.write(bytes.fromhex(packet))
            except ValueError:
                log.warning("非法 init 报文：%s", packet)
                continue
            time.sleep(0.02)

        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline:
            data = handle.read(64, 120)
            if not data or not _match_rule(data, str(rule.get("prefix", "")), str(rule.get("mask", ""))):
                continue
            level = _extract_level(data, rule)
            if level is None:
                continue
            return Reading(
                level=level,
                charging=_extract_charging(data, rule),
                source="vendor",
                ok=True,
                detail=f"厂商报文 {data[:6].hex(' ').upper()}",
            )
        return Reading(detail="未捕获到电量报文", detected=True)


# --------------------------------------------------------------------------- #
# 主动查询式厂商协议（配置驱动）
# --------------------------------------------------------------------------- #
# 实测（漫步者 G3 MAX，35BB:A226，杰理 2.4G dongle）：
#   写 D0 35 BB 01 00（64 字节，其余补 0）
#   收 D0 35 BB 0A 00 01 30 00 … → 第 6 字节 0x30 = 48%，与驱动界面完全一致
# 这类通道不主动上报，必须"问一句答一句"，所以和 read_generic_hid 的被动监听是两条路。
def render_hex_template(template: str, info: HidDeviceInfo) -> bytes:
    """把命令模板里的 {vid_le} / {pid_le} 等占位符换成真实字节。"""
    text = str(template or "")
    text = text.replace("{vid_be}", f"{info.vid >> 8:02X} {info.vid & 0xFF:02X}")
    text = text.replace("{vid_le}", f"{info.vid & 0xFF:02X} {info.vid >> 8:02X}")
    text = text.replace("{pid_be}", f"{info.pid >> 8:02X} {info.pid & 0xFF:02X}")
    text = text.replace("{pid_le}", f"{info.pid & 0xFF:02X} {info.pid >> 8:02X}")
    text = text.replace("{vid}", f"{info.vid:04X}")
    text = text.replace("{pid}", f"{info.pid:04X}")
    if not text.strip():
        return b""
    return bytes.fromhex(text)


def build_vendor_command(template: str, info: HidDeviceInfo, length: int, checksum: str = "") -> bytes:
    """按接口报告长度构造命令：不足补 0，超长截断，最后按需补校验和。"""
    payload = render_hex_template(template, info)
    if length <= 0:
        return payload
    if len(payload) >= length:
        payload = payload[:length]
    else:
        payload = payload + b"\x00" * (length - len(payload))
    return apply_checksum(payload, checksum)


def apply_checksum(payload: bytes, scheme: str) -> bytes:
    """把校验和写进帧尾（覆盖最后一个字节）。

    实测（迈从 G98 PRO，258A:01BB 接收器）：帧长 20，尾字节 = 前 19 字节之和。
        D0 指令帧：13 4A 01 00 02 00 … 00 | 60
    只认明确写在配置里的算法，不认识的方案原样返回 —— 宁可发出去被拒，
    也不要自己编一个算法把设备写坏。
    """
    if not scheme or len(payload) < 2:
        return payload
    body = payload[:-1]
    if scheme == "sum8":
        value = sum(body) & 0xFF
    elif scheme == "xor8":
        value = 0
        for byte in body:
            value ^= byte
    else:
        return payload
    return body + bytes([value])


def _vendor_channels(cfg: DeviceConfig) -> list[dict[str, Any]]:
    """把 protocol 拆成一条或多条通道（channel）。兼容旧版扁平配置。

    当一台设备在无线/有线两种形态下使用不同接口或不同报文格式时，
    用 channels 为每种形态配独立的 match + 读取参数；程序会按顺序尝试，
    命中第一条成功通道就返回。
    """
    proto = cfg.protocol
    channels = proto.get("channels")
    if not channels:
        return [proto]
    base = {k: v for k, v in proto.items() if k != "channels"}
    out: list[dict[str, Any]] = []
    for ch in channels:
        merged = dict(base)
        merged.update(ch)
        out.append(merged)
    return out


def _channel_match(match_cfg: dict[str, Any], channel_match: dict[str, Any]) -> dict[str, Any]:
    """设备级 match 与通道级 match 合并；通道级更具体，覆盖设备级。"""
    merged = dict(match_cfg)
    merged.update(channel_match)
    return merged


def _info_matches_channel(info: HidDeviceInfo, cfg: DeviceConfig, channel: dict[str, Any]) -> bool:
    return DeviceRegistry.matches(info, _channel_match(cfg.match, channel.get("match") or {}))


def parse_vendor_reply(
    data: bytes,
    *,
    prefix: bytes = b"",
    code_offset: Optional[int] = None,
    code_value: Optional[int] = None,
    level_offset: int = -1,
    charging_byte: Optional[int] = None,
    charging_bit: int = 0,
    charging_invert: bool = False,
) -> Optional[tuple[int, Optional[bool]]]:
    """从应答报文里取出 (电量, 是否充电)；不匹配或越界返回 None。"""
    if level_offset < 0 or level_offset >= len(data):
        return None
    if prefix:
        if len(data) < len(prefix) or data[: len(prefix)] != prefix:
            return None
    if code_offset is not None and code_value is not None:
        if code_offset < 0 or code_offset >= len(data) or data[int(code_offset)] != int(code_value):
            return None
    level = data[level_offset]
    if not 0 <= level <= 100:
        return None
    charging: Optional[bool] = None
    if isinstance(charging_byte, int) and 0 <= charging_byte < len(data):
        raw = (data[charging_byte] >> charging_bit) & 0x01
        charging = bool(raw) if not charging_invert else not bool(raw)
    return level, charging


def _build_reading_from_parsed(
    data: bytes,
    channel: dict[str, Any],
    info: HidDeviceInfo,
    source_detail: str,
) -> Reading:
    """把 parse_vendor_reply 的结果转成 Reading；处理 presence / 关机判定。"""
    parsed = parse_vendor_reply(
        data,
        prefix=_render_prefix(channel.get("reply_prefix", ""), info),
        code_offset=channel.get("reply_code_offset"),
        code_value=channel.get("reply_code"),
        level_offset=int(channel.get("level_offset", -1)),
        charging_byte=channel.get("charging_byte"),
        charging_bit=int(channel.get("charging_bit", 0) or 0),
        charging_invert=bool(channel.get("charging_invert")),
    )
    if parsed is None:
        return Reading.fail("")
    level, charging = parsed
    presence = channel.get("presence") or {}
    if level == 0 and isinstance(presence.get("offset"), int):
        offset = int(presence["offset"])
        expected = int(presence.get("value", 1))
        if 0 <= offset < len(data) and data[offset] != expected:
            return Reading(
                offline=True,
                detail=(
                    f"设备已关闭（电量位 0，状态字节 0x{data[offset]:02X}）"
                    f" · {bytes(data[:8]).hex(' ').upper()}"
                ),
            )
    return Reading(
        level=level,
        charging=charging,
        source="vendor-query",
        ok=True,
        detail=f"{source_detail} · {len(data)}B · {bytes(data[:8]).hex(' ').upper()}",
    )


def _render_prefix(template: Any, info: HidDeviceInfo | None) -> bytes:
    """渲染 reply_prefix 模板；info 为 None 时表示已经处理过的字节串。"""
    if isinstance(template, bytes):
        return template
    text = str(template or "")
    if not text.strip():
        return b""
    if info is None:
        return bytes.fromhex(text)
    return render_hex_template(text, info)


def _read_vendor_write_read(
    channel: dict[str, Any], info: HidDeviceInfo, caps: Optional[Any]
) -> Reading:
    """通过 output/input 报告做问答式读取（传统 vendor_query）。"""
    length = int(channel.get("report_length") or (caps.input_length if caps else 64))
    if length <= 0:
        return Reading.fail("接口报告长度未知")

    command = build_vendor_command(
        str(channel.get("command", "")), info, length, str(channel.get("checksum", "") or "")
    )
    prefix = render_hex_template(str(channel.get("reply_prefix", "")), info)
    timeout_ms = int(channel.get("timeout_ms", 300))
    retries = int(channel.get("retries", 1))

    with HidHandle(info) as handle:
        if not handle.is_open:
            return Reading.fail("设备被占用或已拔出")
        for attempt in range(retries + 1):
            handle.drain()
            if handle.write(command) < 0:
                time.sleep(0.04 * (attempt + 1))
                continue
            deadline = time.monotonic() + timeout_ms / 1000.0
            while time.monotonic() < deadline:
                data = handle.read(length, 60)
                if not data:
                    continue
                # 这类 dongle 会把没处理的下行帧原样回显上来，回显帧和命令逐字节相同。
                # 不排掉的话，回显里"电量位"恰好是 0，会被读成 0% —— 比读不到更糟。
                if bytes(data) == command:
                    continue
                reading = _build_reading_from_parsed(data, channel, info, "厂商查询")
                if reading.ok or reading.offline:
                    return reading
            time.sleep(0.03 * (attempt + 1))
    return Reading.fail("厂商查询无应答（设备可能已关机或已断开）")


def _read_vendor_feature(
    channel: dict[str, Any], info: HidDeviceInfo, caps: Optional[Any]
) -> Reading:
    """通过 Feature 报告读取（部分键盘/鼠标的有线模式使用此通道）。"""
    report_id = int(channel.get("feature_id", 0))
    # 解析时按配置 report_length；读取缓冲区按 HID 描述符声明的 feature_length，
    # 两者不一致时取较大值，否则 hidapi 可能因缓冲区不足返回 None。
    parse_length = int(
        channel.get("report_length") or (caps.feature_length if caps else 64)
    )
    read_length = parse_length
    if caps and caps.feature_length > read_length:
        read_length = caps.feature_length
    if read_length <= 0:
        return Reading.fail("Feature 报告长度未知")

    # 有些设备需要先写一条 feature 命令再读；没有 command 时直接读。
    feature_command = b""
    if channel.get("command"):
        feature_command = build_vendor_command(
            str(channel.get("command", "")),
            info,
            parse_length,
            str(channel.get("checksum", "") or ""),
        )

    with HidHandle(info) as handle:
        if not handle.is_open:
            return Reading.fail("设备被占用或已拔出")
        if feature_command:
            if handle.write_feature(feature_command) < 0:
                return Reading.fail("Feature 命令写入失败")
            time.sleep(0.02)
        data = handle.read_feature(report_id, read_length)
        if data:
            reading = _build_reading_from_parsed(data, channel, info, "厂商 Feature")
            if reading.ok or reading.offline:
                return reading
        # 有些通道只是用来确认设备在线，不保证能解析出电量；
        # 只要能打开对应 HID 接口就认为设备存在，读不到电量也返回 detected。
        if channel.get("presence_report"):
            return Reading(
                detected=True,
                charging=bool(channel.get("charging")),
                link=str(channel.get("link", "")),
                source="vendor-query",
                detail="设备在线（电量不可读）" if data else "设备在线（Feature 无应答）",
            )
        if not data:
            return Reading.fail("Feature 报告无应答")
        return Reading.fail("Feature 报告格式不匹配")


def read_vendor_query(cfg: DeviceConfig, info: HidDeviceInfo) -> Reading:
    caps = device_caps(info.path)
    last = Reading.fail("该接口没有匹配的查询通道")

    for channel in _vendor_channels(cfg):
        if not _info_matches_channel(info, cfg, channel):
            continue
        # Feature 通道必须落在真有 Feature 能力的接口上，避免 hidapi 在 feature_length=0 的集合上浪费时间
        if channel.get("feature_report"):
            if not caps or caps.feature_length <= 0:
                last = Reading.fail("接口无 Feature 报告能力")
                continue
            reading = _read_vendor_feature(channel, info, caps)
        else:
            reading = _read_vendor_write_read(channel, info, caps)

        # 通道若显式声明了链路，优先使用它（无线/有线同一设备的场景）。
        if reading.link:
            pass
        elif channel.get("link"):
            reading.link = str(channel["link"])
        elif cfg.protocol.get("link"):
            reading.link = str(cfg.protocol["link"])

        if reading.ok or reading.offline:
            return reading
        last = reading

    return last


def _apply_port_link(reading: Reading, cfg: DeviceConfig, infos: list[HidDeviceInfo]) -> None:
    """针对「接收器与有线手柄共享同一 USB 实例 ID」的设备，用端口判别链路。

    配置示例::

        "link": {
            "mode": "port",
            "wireless_port": "Port_#0011.Hub_#0001",
            "wired_charging": true
        }

    当设备出现在 ``wireless_port`` 时判为 2.4G；出现在其它端口时判为有线。
    若配了 ``wired_charging``，有线时同时置 ``charging=True``
    （这种手柄插线即由 USB 供电，官方驱动也显示充电图标）。

    协议自己已经判定链路（如 HID++）时，不做覆盖。
    """
    if not reading.ok or reading.link:
        return
    link_cfg = cfg.protocol.get("link")
    if not isinstance(link_cfg, dict) or link_cfg.get("mode") != "port":
        return
    candidates = candidate_infos(infos, cfg)
    if not candidates:
        return
    info = candidates[0]
    if not info.serial:
        return
    loc = location_from_vid_pid_serial(info.vid, info.pid, info.serial)
    if loc is None:
        return
    wireless_port = link_cfg.get("wireless_port")
    if loc == wireless_port:
        reading.link = LINK_WIRELESS
    else:
        reading.link = LINK_WIRED
        if link_cfg.get("wired_charging"):
            reading.charging = True


# --------------------------------------------------------------------------- #
# 统一入口
# --------------------------------------------------------------------------- #
def read_device(cfg: DeviceConfig, infos: list[HidDeviceInfo], used_slots: Optional[set[int]] = None) -> Reading:
    reading = _read_device(cfg, infos, used_slots)
    # 链路判断优先级：
    #   1) 协议自己知道（HID++ 索引 0xFF 是有线，1~6 是无线）
    #   2) USB 端口判别（接收器与有线手柄共享 USB 实例 ID 的飞智/迈从类设备）
    #   3) 配置里的静态声明（迈从/漫步者配的是接收器本身 = wireless）
    _apply_port_link(reading, cfg, infos)
    if not reading.link:
        link_cfg = cfg.protocol.get("link", "")
        if isinstance(link_cfg, str):
            reading.link = link_cfg
    return reading


def _read_device(cfg: DeviceConfig, infos: list[HidDeviceInfo], used_slots: Optional[set[int]] = None) -> Reading:
    try:
        name = cfg.protocol_name
        if name == "logitech_hidpp":
            sleeping: Optional[Reading] = None
            detected: Optional[Reading] = None
            wireless_ok: Optional[Reading] = None
            for info in candidate_infos(infos, cfg)[:4]:
                reading = read_logitech(cfg, info)
                if reading.ok:
                    # 同时存在无线接收器和有线接口时，优先使用有线通道，
                    # 避免插线充电时仍显示 2.4G 连接。
                    if reading.link == LINK_WIRED:
                        return reading
                    wireless_ok = wireless_ok or reading
                    continue
                if reading.sleeping:
                    # 这一路休眠不代表别的路不行：插着线的时候鼠标应答在索引 0xFF 上，
                    # 而接收器那边就是休眠/未配对。原来这里直接 return，于是有线变成了「休眠中」。
                    sleeping = sleeping or reading
                    continue
                detected = detected or reading
            return wireless_ok or sleeping or detected or Reading.fail("未找到可用的 HID++ 接口")
        if name == "xinput":
            return xinput.read_device(cfg.protocol.get("slot", "auto"), used_slots if used_slots is not None else set())
        if name == "hid":
            last = Reading.fail("未检测到设备")
            for info in candidate_infos(infos, cfg)[:4]:
                reading = read_generic_hid(cfg, info)
                if reading.ok:
                    return reading
                last = reading
            return last
        if name == "vendor_query":
            last = Reading.fail("未检测到设备")
            candidates = [i for i in candidate_infos(infos, cfg) if i.usage_page >= 0xFF00]
            # 双模设备在无线/有线两种形态下会各暴露多个厂商集合，
            # 必须把搜索范围扩到足够大，否则真正的 feature 接口可能落在第 7、8 位之后。
            for info in candidates[:12]:
                reading = read_vendor_query(cfg, info)
                if reading.ok:
                    return reading
                last = reading
            return last
        return Reading.fail(f"未知协议 {name}")
    except Exception as exc:  # noqa: BLE001
        log.debug("读取 %s 失败：%s", cfg.id, exc)
        return Reading.fail(f"读取异常：{exc}")


def candidate_infos(infos: list[HidDeviceInfo], cfg: DeviceConfig) -> list[HidDeviceInfo]:
    matched = [info for info in infos if DeviceRegistry.matches(info, cfg.match)]
    return sort_preferred(matched)
