"""edifier-g3max 充电位定位工具（临时诊断用，非产品功能）。

HECATE G3 MAX 是「问一句答一句」的主动查询设备：软件发 `D0 35 BB 01 00`
它才应答 `D0 35 BB 0A 00 01 30 00 …`。被动监听抓不到，所以这里主动发查询
并打印原始应答，便于对比「未充电」与「充电中」两种状态的报文差异，定位
真正的充电标志位是哪个字节的哪一位。

用法（先退出本软件，避免 HID 接口被独占）：
    cd "D:/桌面/外设电量显示软件_DeepSeek版"
    .venv/Scripts/python.exe tools/edifier_charging_probe.py

在「未充电」和「充电中」两种状态各跑一次，把两次的完整输出都发回即可。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.hidapi_io import enumerate_devices, HidHandle, sort_preferred
from app.core.protocols import build_vendor_command

VID = 0x35BB
PID = 0xA226
COMMAND = "D0 {vid_be} 01 00"   # 与本软件 edifier-g3max 配置完全一致
REPORT_LEN = 64
SAMPLES = 6


def _byte(data: bytes, offset: int) -> str:
    return f"0x{data[offset]:02X}" if len(data) > offset else "n/a"


def main() -> int:
    infos = [
        i for i in enumerate_devices()
        if i.vid == VID and i.pid == PID and i.usage_page >= 0xFF00
    ]
    if not infos:
        print("未找到 HECATE 厂商接口（VID 35BB / PID A226，UsagePage >= 0xFF00）。")
        print("请确认：① 接收器已插好  ② 本软件已彻底退出（托盘右键退出）")
        print("        ③ 若仍找不到，先运行 tools\\usb_probe.py list 核对接口。")
        return 1

    info = sort_preferred(infos)[0]
    handle = HidHandle(info)
    if not handle.open():
        print(f"无法打开接口（可能被其它程序独占，如官方 HECATE 驱动）：{info.path}")
        print("请先关闭所有可能占用该接收器的程序后再试。")
        return 1

    cmd = build_vendor_command(COMMAND, info, REPORT_LEN, "")
    print(f"设备: {info.product or info.key}  (path={info.path})")
    print(f"查询命令({REPORT_LEN}B): {cmd.hex(' ').upper()}")
    print(f"--- 连续采样 {SAMPLES} 次原始应答（注意 byte5/6/7/8 在两种状态下的变化）---")
    for i in range(SAMPLES):
        handle.drain()
        if handle.write(cmd) < 0:
            print(f"#{i} 写入失败（接口可能被占用）")
            time.sleep(0.05)
            continue
        data = handle.read(REPORT_LEN, 200)
        if not data:
            print(f"#{i} 无应答")
            time.sleep(0.05)
            continue
        print(f"#{i} {data.hex(' ').upper()}")
        print(
            f"     byte5={_byte(data, 5)} byte6={_byte(data, 6)} "
            f"byte7={_byte(data, 7)} byte8={_byte(data, 8)}"
        )
        time.sleep(0.05)
    handle.close()
    print("--- 结束 ---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
