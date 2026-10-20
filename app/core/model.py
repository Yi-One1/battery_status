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

# 链路类型：有线 / 2.4G（接收器）/ 未知
LINK_WIRED = "wired"
LINK_WIRELESS = "wireless"
LINK_LABELS = {LINK_WIRED: "有线", LINK_WIRELESS: "2.4G"}


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
    offline: bool = False    # 明确判定为"设备已关闭"：立刻显示未连接，不走离线去抖
    link: str = ""           # LINK_WIRED / LINK_WIRELESS / 未知

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
    # 当前走的是有线还是 2.4G，"不知道"时留空 —— 界面宁可少说一句，也不猜
    link: str = ""

    @property
    def source_label(self) -> str:
        return SOURCE_LABELS.get(self.source, self.source)

    @property
    def link_label(self) -> str:
        return LINK_LABELS.get(self.link, "")

    @property
    def display_charging(self) -> Optional[bool]:
        """UI 实际应显示的充电状态。

        迈从 G98 PRO 在 2.4G/有线充电时固定把电量位报告成 50% 作为占位符，
        且有些通道不给出充电位。当协议没有明确充电位、但读到 50% 时，
        按充电处理，避免把占位符当成真实电量。
        """
        if self.charging is True:
            return True
        if self.charging is None and self.level == 50 and self.online:
            return True
        return self.charging

    @property
    def display_level(self) -> Optional[int]:
        """UI 实际应显示的电量。

        当设备正在充电且电量位为 50% 时，把它视为充电占位符（迈从 G98 PRO
        固件行为），不显示具体百分比，只显示"充电中"和绿色闪电。
        """
        if self.level == 50 and self.display_charging:
            return None
        return self.level

    def status_text(self, low_threshold: int = 20) -> str:
        if not self.online:
            return "未连接"
        if self.sleeping:
            return "休眠中"
        link = self.link_label
        if self.display_charging:
            return f"{link} · 充电中" if link else "充电中"
        if self.level is None:
            # 读不到电量时，链路本身就是唯一拿得出手的真实信息。
            # 例如飞智 2.4G：硬件根本不输出电量，但"它正连在 2.4G 上"是确凿的。
            # 连链路也不知道时才退回「暂无电量」—— 宁可少说一句，也不猜。
            return f"{link} 连接" if link else "暂无电量"
        if self.level <= low_threshold:
            return "电量偏低"
        # 一切正常时，把"走哪条链路"顶上来 —— 这比一句"使用中"有用得多
        return f"{link} 连接" if link else "使用中"

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
