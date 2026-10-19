"""XInput 手柄电量（Windows 原生 API，四档）。

XInput 只给四档电量，精度有限但零开销、无需抓包，适合手柄类设备兜底。
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes as W
from typing import Optional

from .model import Reading

log = logging.getLogger(__name__)

BATTERY_TYPE_DISCONNECTED = 0
BATTERY_TYPE_WIRED = 1
BATTERY_TYPE_ALKALINE = 2
BATTERY_TYPE_NIMH = 3
BATTERY_TYPE_UNKNOWN = 255

# XInput 的 BatteryLevel 只有四档，按常见 2.4G 手柄的放电区间折算
LEVEL_PERCENT = {0: 5, 1: 25, 2: 60, 3: 100}
LEVEL_TEXT = {0: "空", 1: "低", 2: "中", 3: "满"}


class _BatteryInformation(ctypes.Structure):
    _fields_ = [("BatteryType", W.BYTE), ("BatteryLevel", W.BYTE)]


class _State(ctypes.Structure):
    _fields_ = [
        ("dwPacketNumber", W.DWORD),
        ("wButtons", W.WORD),
        ("bLeftTrigger", W.BYTE),
        ("bRightTrigger", W.BYTE),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


_dll = None
_load_error = ""


def _library():
    global _dll, _load_error
    if _dll is not None or _load_error:
        return _dll
    for name in ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"):
        try:
            lib = ctypes.WinDLL(name)
            lib.XInputGetState.argtypes = [W.DWORD, ctypes.POINTER(_State)]
            lib.XInputGetState.restype = W.DWORD
            lib.XInputGetBatteryInformation.argtypes = [W.DWORD, W.BYTE, ctypes.POINTER(_BatteryInformation)]
            lib.XInputGetBatteryInformation.restype = W.DWORD
            _dll = lib
            return _dll
        except Exception:  # noqa: BLE001
            continue
    _load_error = "系统缺少 XInput 运行库"
    return None


def connected_slots() -> list[int]:
    lib = _library()
    if lib is None:
        return []
    slots: list[int] = []
    for index in range(4):
        state = _State()
        if lib.XInputGetState(index, ctypes.byref(state)) == 0:
            slots.append(index)
    return slots


def read_slot(slot: int) -> Reading:
    lib = _library()
    if lib is None:
        return Reading.fail(_load_error or "系统缺少 XInput 运行库")
    state = _State()
    if lib.XInputGetState(slot, ctypes.byref(state)) != 0:
        return Reading.fail("手柄未连接")

    info = _BatteryInformation()
    if lib.XInputGetBatteryInformation(slot, 0, ctypes.byref(info)) != 0:
        return Reading.fail("XInput 未提供电池信息")

    if info.BatteryType == BATTERY_TYPE_WIRED:
        # 2.4G 接收器会把自己伪装成"有线供电"的 Xbox 手柄，Windows 固定报"满"档。
        # 这种接入方式下：既没有充电的证据（绝大多数时候根本没插线），
        # 也没有任何可用的电量档位 —— 所以两个字段都留空，界面会显示「暂无电量」，
        # 而不是原来那句会误导人的「充电中」。
        return Reading(
            level=None,
            charging=None,
            source="xinput",
            ok=True,
            detail=f"手柄 {slot} · 有线/接收器接入，XInput 不提供电量档位",
        )
    if info.BatteryType == BATTERY_TYPE_DISCONNECTED:
        return Reading.fail("手柄已断开")

    percent = LEVEL_PERCENT.get(int(info.BatteryLevel))
    if percent is None:
        # 超出 EMPTY/LOW/MEDIUM/FULL 四档：宁可没有数字，也不要编一个 100
        return Reading(
            level=None,
            charging=False,
            source="xinput",
            ok=True,
            detail=f"手柄 {slot} · XInput 返回了未知电量档位 0x{int(info.BatteryLevel):02X}",
        )
    return Reading(
        level=percent,
        charging=False,
        source="xinput",
        ok=True,
        detail=f"手柄 {slot} · XInput 电量「{LEVEL_TEXT.get(int(info.BatteryLevel), '?')}」四档折算",
    )


def read_device(slot_setting: object, used_slots: set[int]) -> Reading:
    """slot_setting 支持具体槽位或 "auto"（取第一个未被占用的已连接手柄）。"""
    slots = connected_slots()
    if not slots:
        return Reading.fail("未检测到 XInput 手柄")
    if isinstance(slot_setting, int):
        return read_slot(slot_setting)
    for slot in slots:
        if slot not in used_slots:
            used_slots.add(slot)
            return read_slot(slot)
    return Reading.fail("所有手柄槽位都已被占用")
