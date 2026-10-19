"""HID 原生能力（ctypes）：解析报告描述符，读取标准 Battery Strength。

思路参考 USB Device Viewer：先看设备暴露了哪些 Usage，再决定怎么读。
hidapi 只负责字节流，HID 语义（Usage Page / Usage）由 hid.dll 的
HidP_* 系列 API 提供，不需要第三方库。

关键点：HIDP_VALUE_CAPS 不暴露位偏移，但 HidP_GetUsageValue 会按预解析
（preparsed data）自动定位字段，因此我们直接让它从原始报文里取 Usage 值。
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes as W
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# HIDP_STATUS_SUCCESS = 0x00110000，NTSTATUS 风格，不是 0
HIDP_STATUS_SUCCESS = 0x00110000

HIDP_INPUT = 0
HIDP_OUTPUT = 1
HIDP_FEATURE = 2

USAGE_PAGE_GENERIC_DEVICE_CONTROLS = 0x06
USAGE_BATTERY_STRENGTH = 0x20

_AVAILABLE = True
try:
    _hid_dll = ctypes.WinDLL("hid.dll")
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
except Exception as exc:  # noqa: BLE001
    _AVAILABLE = False
    _hid_dll = None
    _kernel32 = None
    log.warning("hid.dll 不可用：%s", exc)


def _hid_ok(status: int) -> bool:
    """HIDP_STATUS_SUCCESS = 0x00110000（NTSTATUS 风格），不能直接与 0 比较。"""
    return (int(status) & 0xFFFF0000) == HIDP_STATUS_SUCCESS


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", ctypes.c_ushort),
        ("UsagePage", ctypes.c_ushort),
        ("InputReportByteLength", ctypes.c_ushort),
        ("OutputReportByteLength", ctypes.c_ushort),
        ("FeatureReportByteLength", ctypes.c_ushort),
        ("Reserved", ctypes.c_ushort * 17),
        ("NumberLinkCollectionNodes", ctypes.c_ushort),
        ("NumberInputButtonCaps", ctypes.c_ushort),
        ("NumberInputValueCaps", ctypes.c_ushort),
        ("NumberInputDataIndices", ctypes.c_ushort),
        ("NumberOutputButtonCaps", ctypes.c_ushort),
        ("NumberOutputValueCaps", ctypes.c_ushort),
        ("NumberOutputDataIndices", ctypes.c_ushort),
        ("NumberFeatureButtonCaps", ctypes.c_ushort),
        ("NumberFeatureValueCaps", ctypes.c_ushort),
        ("NumberFeatureDataIndices", ctypes.c_ushort),
    ]


class HIDP_VALUE_CAPS(ctypes.Structure):
    _fields_ = [
        ("UsagePage", ctypes.c_ushort),
        ("ReportID", ctypes.c_ubyte),
        ("IsAlias", ctypes.c_ubyte),
        ("BitField", ctypes.c_ushort),
        ("LinkCollection", ctypes.c_ushort),
        ("LinkUsagePage", ctypes.c_ushort),
        ("LinkUsage", ctypes.c_ushort),
        ("IsRange", ctypes.c_ubyte),
        ("IsStringRange", ctypes.c_ubyte),
        ("IsDesignatorRange", ctypes.c_ubyte),
        ("IsAbsolute", ctypes.c_ubyte),
        ("BitSize", ctypes.c_ushort),
        ("ReportCount", ctypes.c_ushort),
        ("Reserved2", ctypes.c_ushort * 5),
        ("UnitsExp", ctypes.c_ulong),
        ("Units", ctypes.c_ulong),
        ("LogicalMin", ctypes.c_long),
        ("LogicalMax", ctypes.c_long),
        ("PhysicalMin", ctypes.c_long),
        ("PhysicalMax", ctypes.c_long),
        ("Union", ctypes.c_ushort * 8),
    ]


if _AVAILABLE:
    _hid_dll.HidD_GetPreparsedData.argtypes = [W.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    _hid_dll.HidD_GetPreparsedData.restype = W.BOOLEAN
    _hid_dll.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
    _hid_dll.HidD_FreePreparsedData.restype = W.BOOLEAN
    _hid_dll.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
    _hid_dll.HidP_GetCaps.restype = ctypes.c_long
    _hid_dll.HidP_GetValueCaps.argtypes = [
        ctypes.c_int,
        ctypes.POINTER(HIDP_VALUE_CAPS),
        ctypes.POINTER(ctypes.c_ushort),
        ctypes.c_void_p,
    ]
    _hid_dll.HidP_GetValueCaps.restype = ctypes.c_long
    _hid_dll.HidP_GetUsageValue.argtypes = [
        ctypes.c_int,
        ctypes.c_ushort,
        ctypes.c_ushort,
        ctypes.c_ushort,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.c_ulong,
    ]
    _hid_dll.HidP_GetUsageValue.restype = ctypes.c_long

    _kernel32.CreateFileW.argtypes = [
        W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, W.HANDLE
    ]
    _kernel32.CreateFileW.restype = W.HANDLE
    _kernel32.CloseHandle.argtypes = [W.HANDLE]
    _kernel32.CloseHandle.restype = W.BOOL


@dataclass
class ValueCap:
    usage_page: int
    usages: list[int]
    report_id: int
    link_collection: int
    bit_size: int
    report_count: int
    logical_min: int
    logical_max: int
    is_range: bool

    def as_dict(self) -> dict:
        return {
            "usage_page": f"0x{self.usage_page:04X}",
            "usage": ", ".join(f"0x{u:02X}" for u in self.usages),
            "report_id": f"0x{self.report_id:02X}",
            "bit_size": self.bit_size,
            "logical": f"{self.logical_min}..{self.logical_max}",
        }


@dataclass
class DeviceCaps:
    usage_page: int = 0
    usage: int = 0
    input_length: int = 0
    output_length: int = 0
    feature_length: int = 0
    value_caps: list[ValueCap] = field(default_factory=list)

    def has_battery(self) -> bool:
        return any(
            cap.usage_page == USAGE_PAGE_GENERIC_DEVICE_CONTROLS and USAGE_BATTERY_STRENGTH in cap.usages
            for cap in self.value_caps
        )


class _NativeHandle:
    """通过设备接口路径拿到预解析数据（与 hidapi 的句柄互不影响）。"""

    def __init__(self, path: str) -> None:
        self.path = path
        self.handle = None
        self.preparsed = ctypes.c_void_p()
        self.caps = HIDP_CAPS()

    def __enter__(self) -> Optional["_NativeHandle"]:
        if not _AVAILABLE:
            return None
        access = GENERIC_READ | GENERIC_WRITE
        handle = _kernel32.CreateFileW(
            self.path, access, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None
        )
        if handle == INVALID_HANDLE_VALUE or handle is None:
            handle = _kernel32.CreateFileW(
                self.path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None
            )
        if handle == INVALID_HANDLE_VALUE or handle is None:
            return None
        self.handle = handle
        if not _hid_dll.HidD_GetPreparsedData(handle, ctypes.byref(self.preparsed)):
            self.close()
            return None
        if not _hid_ok(_hid_dll.HidP_GetCaps(self.preparsed, ctypes.byref(self.caps))):
            self.close()
            return None
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        if self.preparsed and self.preparsed.value and _AVAILABLE:
            try:
                _hid_dll.HidD_FreePreparsedData(self.preparsed)
            except Exception:  # noqa: BLE001
                pass
        self.preparsed = ctypes.c_void_p()
        if self.handle not in (None, INVALID_HANDLE_VALUE) and _AVAILABLE:
            try:
                _kernel32.CloseHandle(self.handle)
            except Exception:  # noqa: BLE001
                pass
        self.handle = None

    # -- 查询 ---------------------------------------------------------------
    def value_caps(self, report_type: int = HIDP_INPUT) -> list[ValueCap]:
        out: list[ValueCap] = []
        if not self.preparsed or not self.preparsed.value:
            return out
        length = ctypes.c_ushort(0)
        if report_type == HIDP_INPUT:
            length = ctypes.c_ushort(self.caps.NumberInputValueCaps)
        elif report_type == HIDP_OUTPUT:
            length = ctypes.c_ushort(self.caps.NumberOutputValueCaps)
        else:
            length = ctypes.c_ushort(self.caps.NumberFeatureValueCaps)
        if length.value == 0:
            return out
        buffer = (HIDP_VALUE_CAPS * length.value)()
        status = _hid_dll.HidP_GetValueCaps(report_type, buffer, ctypes.byref(length), self.preparsed)
        if not _hid_ok(status):
            return out
        for index in range(length.value):
            item = buffer[index]
            if item.IsRange:
                usages = list(range(item.Union[0], item.Union[1] + 1))
            else:
                usages = [item.Union[0]]
            out.append(
                ValueCap(
                    usage_page=item.UsagePage,
                    usages=usages,
                    report_id=item.ReportID,
                    link_collection=item.LinkCollection,
                    bit_size=item.BitSize,
                    report_count=item.ReportCount,
                    logical_min=item.LogicalMin,
                    logical_max=item.LogicalMax,
                    is_range=bool(item.IsRange),
                )
            )
        return out

    def usage_value(self, report: bytes, usage_page: int, usage: int, link_collection: int = 0) -> Optional[int]:
        if not self.preparsed or not self.preparsed.value or not report:
            return None
        buffer = (ctypes.c_ubyte * len(report)).from_buffer_copy(report)
        value = ctypes.c_ulong(0)
        status = _hid_dll.HidP_GetUsageValue(
            HIDP_INPUT,
            usage_page,
            link_collection,
            usage,
            ctypes.byref(value),
            self.preparsed,
            buffer,
            len(report),
        )
        if not _hid_ok(status):
            return None
        return int(value.value)


def device_caps(path: bytes | str) -> Optional[DeviceCaps]:
    """返回设备的 HID 能力摘要（含全部输入 Value Caps）。"""
    if not _AVAILABLE:
        return None
    text = path.decode("utf-8", "ignore") if isinstance(path, bytes) else str(path)
    with_value = _NativeHandle(text)
    handle = with_value.__enter__()
    if handle is None:
        return None
    try:
        caps = DeviceCaps(
            usage_page=handle.caps.UsagePage,
            usage=handle.caps.Usage,
            input_length=handle.caps.InputReportByteLength,
            output_length=handle.caps.OutputReportByteLength,
            feature_length=handle.caps.FeatureReportByteLength,
            value_caps=handle.value_caps(HIDP_INPUT),
        )
        return caps
    finally:
        with_value.close()


def read_battery_strength(path: bytes | str, report: bytes) -> Optional[int]:
    """从一条输入报文里按标准 HID Battery Strength（0x06/0x20）取值。"""
    if not _AVAILABLE or not report:
        return None
    text = path.decode("utf-8", "ignore") if isinstance(path, bytes) else str(path)
    guard = _NativeHandle(text)
    handle = guard.__enter__()
    if handle is None:
        return None
    try:
        caps = handle.value_caps(HIDP_INPUT)
        candidates = [
            cap
            for cap in caps
            if cap.usage_page == USAGE_PAGE_GENERIC_DEVICE_CONTROLS and USAGE_BATTERY_STRENGTH in cap.usages
        ]
        if not candidates:
            return None
        # 只处理与当前报文 report id 匹配的能力
        current_id = report[0] if report else 0
        preferred = [c for c in candidates if c.report_id in (0, current_id)] or candidates
        for cap in preferred:
            for link in {0, cap.link_collection}:
                value = handle.usage_value(report, cap.usage_page, USAGE_BATTERY_STRENGTH, link)
                if value is not None:
                    logical_max = cap.logical_max if cap.logical_max > 0 else 100
                    if logical_max != 100 and logical_max > 0:
                        value = round(value * 100 / logical_max)
                    return max(0, min(100, int(value)))
        return None
    finally:
        guard.close()


# --------------------------------------------------------------------------- #
# 原始报告描述符（用于逆向新设备）
# --------------------------------------------------------------------------- #
IOCTL_HID_GET_REPORT_DESCRIPTOR = 0x000B0000


def report_descriptor(path: bytes | str) -> bytes:
    """读取 HID 原始报告描述符（USB Device Viewer 里的 Descriptor 一栏）。"""
    if not _AVAILABLE:
        return b""
    text = path.decode("utf-8", "ignore") if isinstance(path, bytes) else str(path)
    guard = _NativeHandle(text)
    handle = guard.__enter__()
    if handle is None:
        return b""
    try:
        _kernel32.DeviceIoControl.argtypes = [
            W.HANDLE, W.DWORD, ctypes.c_void_p, W.DWORD, ctypes.c_void_p, W.DWORD,
            ctypes.POINTER(W.DWORD), ctypes.c_void_p,
        ]
        _kernel32.DeviceIoControl.restype = W.BOOL
        buffer = ctypes.create_string_buffer(4096)
        returned = W.DWORD(0)
        ok = _kernel32.DeviceIoControl(
            handle.handle,
            IOCTL_HID_GET_REPORT_DESCRIPTOR,
            None,
            0,
            buffer,
            4096,
            ctypes.byref(returned),
            None,
        )
        if not ok or returned.value == 0:
            return b""
        return buffer.raw[: returned.value]
    finally:
        guard.close()
