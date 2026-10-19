"""从 Windows 注册表读取 USB 设备节点的物理端口信息。

某些 Xbox 兼容手柄的 2.4G 接收器与有线手柄共用同一个 USB 设备实例 ID
（相同的 VID/PID/序列号），因此无法通过描述符区分它们。
Windows 用同一个注册表键承载两者，但会更新键里的 LocationInformation
（即 USB 端口）。所以我们可以用端口作为「无线/有线」的判据。
"""
from __future__ import annotations

import winreg
from typing import Optional


_LM_ENUM = r"SYSTEM\CurrentControlSet\Enum\USB\VID_{vid:04X}&PID_{pid:04X}\{serial}"


def location_from_vid_pid_serial(vid: int, pid: int, serial: str) -> Optional[str]:
    """读取指定 USB 设备实例的 LocationInformation。

    返回值示例：``Port_#0011.Hub_#0001``。
    若键不存在或读取出错，返回 ``None``。
    """
    if not serial:
        return None
    key_path = _LM_ENUM.format(vid=vid, pid=pid, serial=serial)
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as k:
            value, _ = winreg.QueryValueEx(k, "LocationInformation")
            return value if isinstance(value, str) else None
    except (OSError, FileNotFoundError):
        return None
