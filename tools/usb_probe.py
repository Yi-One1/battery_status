"""外设诊断 / 抓包工具（免安装，命令行）。

用途：给"新设备接入"提供证据链——先看清接口有哪些 Usage，再抓原始报文，
定位电量字节后填进 app/config/devices.json 的 protocol.parse，不用改代码。

    python tools/usb_probe.py list
    python tools/usb_probe.py list --battery
    python tools/usb_probe.py logitech
    python tools/usb_probe.py watch --vid 0x258A --pid 0x01BB
    python tools/usb_probe.py watch --vid 0x35BB --pid 0xA226 --json out.json
    python tools/usb_probe.py feature --vid 0x258A --pid 0x01BB
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.hidapi_io import HidDeviceInfo, HidHandle, enumerate_devices, sort_preferred
from app.core.hidnative import USAGE_BATTERY_STRENGTH, USAGE_PAGE_GENERIC_DEVICE_CONTROLS, device_caps


def _hex(value: int) -> str:
    return f"0x{value:04X}"


def _filter(infos: list[HidDeviceInfo], vid: int | None, pid: int | None) -> list[HidDeviceInfo]:
    out = infos
    if vid is not None:
        out = [i for i in out if i.vid == vid]
    if pid is not None:
        out = [i for i in out if i.pid == pid]
    return out


# --------------------------------------------------------------------------- #
# list
# --------------------------------------------------------------------------- #
def cmd_list(args: argparse.Namespace) -> int:
    infos = enumerate_devices()
    if not infos:
        print("没有枚举到 HID 设备（hidapi 是否安装？）")
        return 1
    rows = []
    for info in sorted(infos, key=lambda i: (i.vid, i.pid, i.usage_page, i.usage)):
        caps = device_caps(info.path)
        has_battery = bool(caps and caps.has_battery())
        if args.battery and not has_battery:
            continue
        rows.append((info, caps, has_battery))

    print(f"{'设备':<28}{'VID:PID':<14}{'UP':>7}{'U':>6}{'IN':>5}{'OUT':>5}{'FEAT':>6}  电量页")
    print("-" * 90)
    for info, caps, has_battery in rows:
        name = (info.product or info.manufacturer or "-")[:26]
        print(
            f"{name:<28}{info.key:<14}{_hex(info.usage_page):>7}{'0x%02X' % info.usage:>6}"
            f"{(caps.input_length if caps else 0):>5}"
            f"{(caps.output_length if caps else 0):>5}"
            f"{(caps.feature_length if caps else 0):>6}  {'✔ 可用' if has_battery else '-'}"
        )
    print("-" * 90)
    print("提示：UP 为 0xFF00 以上通常是厂商自定义接口；电量页为 ✔ 的设备可直接读电量。")
    return 0


# --------------------------------------------------------------------------- #
# watch
# --------------------------------------------------------------------------- #
def _candidate_bytes(frames: list[bytes], report_len: int) -> list[tuple[int, int]]:
    """找出所有帧都落在 1..100、且不同帧之间保持不变的字节位置。"""
    if not frames:
        return []
    out = []
    for offset in range(1, report_len):
        values = {f[offset] for f in frames if len(f) > offset}
        if len(values) == 1:
            value = next(iter(values))
            if 1 <= value <= 100:
                out.append((offset, value))
    return out


def cmd_watch(args: argparse.Namespace) -> int:
    vid = int(args.vid, 0) if args.vid else None
    pid = int(args.pid, 0) if args.pid else None
    targets = sort_preferred(_filter(enumerate_devices(), vid, pid))
    if not targets:
        print("没有匹配的设备，先运行 list 确认 VID/PID。")
        return 1

    summary = []
    for info in targets:
        caps = device_caps(info.path)
        report_len = caps.input_length if caps else 0
        if report_len <= 0:
            continue
        handle = HidHandle(info)
        if not handle.open():
            continue
        print(f"\n=== {info.product or info.key}  UP={_hex(info.usage_page)}  IN={report_len}B ===")
        if args.init:
            try:
                handle.write(bytes.fromhex(args.init))
                print(f"    已发送唤醒报文：{args.init}")
            except ValueError:
                print("    init 报文格式错误，需形如 07 02 00 00")
        frames: dict[bytes, int] = {}
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            data = handle.read(report_len, 120)
            if data:
                frames[data] = frames.get(data, 0) + 1
        handle.close()

        if not frames:
            print("    没有收到输入报文。设备可能只在上报事件时发送数据，可尝试操作一下设备。")
            continue
        for frame, count in list(frames.items())[: args.max_frames]:
            print(f"    x{count:<4} {frame.hex(' ')}")
        candidates = _candidate_bytes(list(frames), report_len)
        if candidates:
            print("    候选电量字节（值 1-100 且稳定）：", ", ".join(f"[{o}]={v}" for o, v in candidates[:8]))
        summary.append(
            {
                "vid": _hex(info.vid),
                "pid": _hex(info.pid),
                "usage_page": _hex(info.usage_page),
                "usage": f"0x{info.usage:02X}",
                "report_len": report_len,
                "frames": [f.hex(" ") for f in frames],
                "candidates": [{"offset": o, "value": v} for o, v in candidates],
            }
        )

    if args.json and summary:
        Path(args.json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写出：{args.json}")
    print(
        "\n下一步：把电量字节偏移填到 app/config/devices.json 里对应设备的 protocol.parse，例如\n"
        '  "parse": {"prefix": "5A A5", "offset": 4, "scale": 1, "charging_byte": 5, "charging_bit": 1}'
    )
    return 0


# --------------------------------------------------------------------------- #
# feature
# --------------------------------------------------------------------------- #
def cmd_feature(args: argparse.Namespace) -> int:
    vid = int(args.vid, 0) if args.vid else None
    pid = int(args.pid, 0) if args.pid else None
    for info in _filter(enumerate_devices(), vid, pid):
        caps = device_caps(info.path)
        if not caps or caps.feature_length <= 0:
            continue
        handle = HidHandle(info)
        if not handle.open():
            continue
        print(f"\n=== {info.product or info.key}  UP={_hex(info.usage_page)}  FEAT={caps.feature_length}B ===")
        for report_id in range(args.start, args.end + 1):
            try:
                data = handle.read_feature(report_id, caps.feature_length)
            except Exception:  # noqa: BLE001
                data = None
            if data:
                print(f"    feat 0x{report_id:02X} ({len(data)}B)  {data[:32].hex(' ')}")
        handle.close()
    return 0


# --------------------------------------------------------------------------- #
# logitech
# --------------------------------------------------------------------------- #
def cmd_logitech(args: argparse.Namespace) -> int:
    from app.core.protocols import HidppSession, hidpp_probe, LONG_MESSAGE_SIZE, SHORT_MESSAGE_SIZE

    targets = [i for i in enumerate_devices() if i.vid == 0x046D]
    if not targets:
        print("没有发现罗技接收器。")
        return 1
    for info in sort_preferred(targets):
        caps = device_caps(info.path)
        if not caps or caps.input_length not in (SHORT_MESSAGE_SIZE, LONG_MESSAGE_SIZE):
            continue
        handle = HidHandle(info)
        if not handle.open():
            continue
        session = HidppSession(handle, caps.input_length)
        probe = hidpp_probe(session, info.path, force=True)
        print(f"\n=== UP={_hex(info.usage_page)} IN={caps.input_length}B ===")
        if probe.asleep:
            print("    有线/无线设备处于休眠，唤醒后重试")
        for device in probe.devices:
            print(f"    idx={device.index:#04x} name={device.name!r} wired={device.wired}")
            reading = session.read_battery(device.index)
            print(f"      battery: level={reading.level} charging={reading.charging} detail={reading.detail}")
        if not probe.devices:
            print("    未发现配对设备")
        handle.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="2.4G 外设 HID 诊断工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出全部 HID 接口及能力")
    p_list.add_argument("--battery", action="store_true", help="只看暴露标准电量页的设备")
    p_list.set_defaults(func=cmd_list)

    p_watch = sub.add_parser("watch", help="抓取厂商接口的输入报文")
    p_watch.add_argument("--vid")
    p_watch.add_argument("--pid")
    p_watch.add_argument("--seconds", type=float, default=6.0)
    p_watch.add_argument("--max-frames", type=int, default=12)
    p_watch.add_argument("--init", default="", help="抓包前先发送的唤醒报文（十六进制）")
    p_watch.add_argument("--json", default="", help="把结果写成 JSON")
    p_watch.set_defaults(func=cmd_watch)

    p_feature = sub.add_parser("feature", help="枚举 Feature 报文（只读）")
    p_feature.add_argument("--vid")
    p_feature.add_argument("--pid")
    p_feature.add_argument("--start", type=int, default=0)
    p_feature.add_argument("--end", type=int, default=15)
    p_feature.set_defaults(func=cmd_feature)

    p_logi = sub.add_parser("logitech", help="探测罗技 HID++ 通道与电量")
    p_logi.set_defaults(func=cmd_logitech)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
