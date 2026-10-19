"""HID 访问层：基于 hidapi 的设备枚举与句柄封装。

上层（协议层）只关心「打开 -> 读写 -> 关闭」，与具体 USB 细节解耦。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

try:  # hidapi 缺失时仍允许 UI 以演示模式启动
    import hid as _hid  # type: ignore
    HIDAPI_AVAILABLE = True
    HIDAPI_ERROR = ""
except Exception as exc:  # noqa: BLE001
    _hid = None  # type: ignore
    HIDAPI_AVAILABLE = False
    HIDAPI_ERROR = str(exc)


@dataclass(frozen=True)
class HidDeviceInfo:
    path: bytes
    vid: int
    pid: int
    product: str = ""
    manufacturer: str = ""
    serial: str = ""
    usage_page: int = 0
    usage: int = 0
    interface: int = -1
    release: int = 0

    @property
    def key(self) -> str:
        return f"{self.vid:04X}:{self.pid:04X}"

    @property
    def title(self) -> str:
        return self.product or self.manufacturer or self.key


def _decode(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "replace").strip("\x00").strip()
        except Exception:  # noqa: BLE001
            return ""
    return str(value).strip()


def enumerate_devices() -> list[HidDeviceInfo]:
    """枚举当前系统全部 HID 设备（含多接口）。"""
    if not HIDAPI_AVAILABLE:
        return []
    result: list[HidDeviceInfo] = []
    try:
        raw = _hid.enumerate()
    except Exception as exc:  # noqa: BLE001
        log.warning("HID 枚举失败：%s", exc)
        return []
    for item in raw:
        try:
            result.append(
                HidDeviceInfo(
                    path=item.get("path") or b"",
                    vid=int(item.get("vendor_id") or 0),
                    pid=int(item.get("product_id") or 0),
                    product=_decode(item.get("product_string")),
                    manufacturer=_decode(item.get("manufacturer_string")),
                    serial=_decode(item.get("serial_number")),
                    usage_page=int(item.get("usage_page") or 0),
                    usage=int(item.get("usage") or 0),
                    interface=int(item.get("interface_number") if item.get("interface_number") is not None else -1),
                    release=int(item.get("release_number") or 0),
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return result


class HidHandle:
    """一次打开的设备句柄。所有读写都带超时，避免阻塞监测线程。"""

    def __init__(self, info: HidDeviceInfo) -> None:
        self.info = info
        self._dev = None

    def open(self) -> bool:
        if not HIDAPI_AVAILABLE:
            return False
        try:
            dev = _hid.device()
            dev.open_path(self.info.path)
            try:
                dev.set_nonblocking(False)
            except Exception:  # noqa: BLE001
                pass
            self._dev = dev
            return True
        except Exception as exc:  # noqa: BLE001
            log.debug("打开 HID 设备失败 %s: %s", self.info.title, exc)
            self._dev = None
            return False

    @property
    def is_open(self) -> bool:
        return self._dev is not None

    def write(self, payload: bytes) -> int:
        if not self._dev:
            return -1
        try:
            return int(self._dev.write(list(payload)))
        except Exception as exc:  # noqa: BLE001
            log.debug("HID 写入失败：%s", exc)
            return -1

    def read(self, size: int = 64, timeout_ms: int = 150) -> Optional[bytes]:
        if not self._dev:
            return None
        try:
            data = self._dev.read(size, timeout_ms)
        except Exception as exc:  # noqa: BLE001
            log.debug("HID 读取失败：%s", exc)
            return None
        if not data:
            return None
        return bytes(data)

    def read_feature(self, report_id: int, length: int = 64) -> Optional[bytes]:
        """读取 Feature 报告（只读诊断用）。"""
        if not self._dev:
            return None
        try:
            data = self._dev.get_feature_report(report_id, length)
        except Exception:  # noqa: BLE001
            return None
        return bytes(data) if data else None

    def drain(self, size: int = 64, timeout_ms: int = 30, limit: int = 12) -> list[bytes]:
        """清掉缓冲区里残留的上报，保证后续读到的是新鲜数据。"""
        out: list[bytes] = []
        for _ in range(limit):
            data = self.read(size, timeout_ms)
            if data is None:
                break
            out.append(data)
        return out

    def close(self) -> None:
        if not self._dev:
            return
        try:
            self._dev.close()
        except Exception:  # noqa: BLE001
            pass
        self._dev = None

    def __enter__(self) -> "HidHandle":
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def sort_preferred(infos: list[HidDeviceInfo]) -> list[HidDeviceInfo]:
    """优先尝试厂商自定义 Usage Page（0xFF00 及以上）的接口，HID++ 通常在此。"""
    return sorted(infos, key=lambda i: (0 if i.usage_page >= 0xFF00 else 1, i.interface))


def sleep_ms(ms: int) -> None:
    time.sleep(ms / 1000.0)
