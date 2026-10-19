"""数据模型：单次读数与对外快照。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

SOURCE_LABELS = {
    "hidpp": "HID++",
    "hid-battery": "HID 电量页",
    "vendor": "厂商报文",
    "demo": "演示数据",
    "none": "未读取",
}


@dataclass
class Reading:
    """一次协议读取的结果。"""

    level: Optional[int] = None
    charging: Optional[bool] = None
    detail: str = ""
    source: str = "none"
    ok: bool = False
    sleeping: bool = False
    detected: bool = False   # 设备存在但读不到电量，用于区分「未连接」

    @classmethod
    def fail(cls, detail: str = "", sleeping: bool = False) -> "Reading":
        return cls(level=None, charging=None, detail=detail, source="none", ok=False, sleeping=sleeping)


@dataclass
class Snapshot:
    """面向 UI 的设备状态快照。"""

    device_id: str
    level: Optional[int] = None
    charging: Optional[bool] = None
    online: bool = False
    source: str = "none"
    detail: str = ""
    updated_at: float = 0.0
    history: list[int] = field(default_factory=list)
    sleeping: bool = False
    # 最后一次"看见"这台设备的时间（读成功、检测到、或休眠都算）。
    # updated_at 只在读到电量时前进，用它判断断连会把"一直读不到电量"的设备误判成离线。
    last_seen: float = 0.0

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)

    def status_text(self, low_threshold: int = 20) -> str:
        if not self.online:
            return "未连接"
        if self.sleeping:
            return "休眠中"
        if self.charging:
            return "充电中"
        if self.level is None:
            return "暂无电量"
        if self.level <= low_threshold:
            return "电量偏低"
        return "使用中"

    def age_text(self) -> str:
        if not self.updated_at:
            return "未同步"
        delta = max(0, int(time.time() - self.updated_at))
        if delta < 10:
            return "刚刚同步"
        if delta < 60:
            return f"{delta} 秒前"
        if delta < 3600:
            return f"{delta // 60} 分钟前"
        return f"{delta // 3600} 小时前"
