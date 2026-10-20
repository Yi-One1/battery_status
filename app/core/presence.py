"""在线判定：断连多久之后，这台设备就不该再占着面板和托盘的坑位。

问题3 里的"自动删除断连设备"用这个模块实现。刻意做成纯函数而不是塞进监测线程：
  * 面板、托盘、设置页三处都要用同一套规则，口径必须完全一致；
  * 规则本身可以脱离硬件单测（喂几个 Snapshot 就能验收）。

三条判定依次短路：
  1. 还没扫到过（snapshots 里没有）→ 保留占位，避免界面刚起来时空一片；
  2. 在线 / 休眠 → 一定保留。休眠的鼠标仍然是连着的那只鼠标；
  3. 离线 → 从 last_seen 起算，超过 offline_hide_minutes 才收起。
"""
from __future__ import annotations

import time
from typing import Iterable, Optional

from .config import DEFAULT_OFFLINE_HIDE_MINUTES, DeviceConfig, Preferences
from .model import Snapshot


def offline_seconds(snapshot: Snapshot, now: Optional[float] = None) -> float:
    """距最后一次"看见"这台设备过了多少秒；从未见过返回 inf。"""
    now = time.time() if now is None else now
    anchor = snapshot.last_seen or snapshot.updated_at
    if not anchor:
        return float("inf")
    return max(0.0, float(now) - float(anchor))


def is_present(
    snapshot: Optional[Snapshot],
    *,
    now: Optional[float] = None,
    hide_minutes: int = DEFAULT_OFFLINE_HIDE_MINUTES,
) -> bool:
    """这台设备是否还应出现在界面上。"""
    if snapshot is None:
        return True
    if snapshot.online or snapshot.sleeping:
        return True
    if hide_minutes <= 0:
        return True
    return offline_seconds(snapshot, now) < hide_minutes * 60


def effective_hide_minutes(prefs: Preferences) -> int:
    if not prefs.auto_hide_offline:
        return 0
    return max(1, int(prefs.offline_hide_minutes))


def filter_visible(
    devices: Iterable[DeviceConfig],
    snapshots: dict[str, Snapshot],
    prefs: Preferences,
    *,
    now: Optional[float] = None,
) -> list[DeviceConfig]:
    """按「自动收起断连设备」规则过滤出要显示的设备。"""
    minutes = effective_hide_minutes(prefs)
    if minutes <= 0:
        return [d for d in devices if d.enabled]
    return [d for d in devices if d.enabled and is_present(snapshots.get(d.id), now=now, hide_minutes=minutes)]


def ordered_devices(devices: Iterable[DeviceConfig], prefs: Preferences) -> list[DeviceConfig]:
    """按用户自定义顺序（prefs.device_order）排列设备。

    排在 device_order 里的在前，顺序即列表顺序；
    没列出的设备跟在后面，保持原有（内置）相对顺序 —— 面板与托盘必须共用
    这一个函数，两边的排列才会一致。
    """
    items = list(devices)
    order = [str(x) for x in (prefs.device_order or [])]
    if not order:
        return items
    rank = {device_id: index for index, device_id in enumerate(order)}
    fallback = len(rank)
    ranked = sorted(enumerate(items), key=lambda pair: (rank.get(pair[1].id, fallback), pair[0]))
    return [device for _, device in ranked]


def hidden_devices(
    devices: Iterable[DeviceConfig],
    snapshots: dict[str, Snapshot],
    prefs: Preferences,
    *,
    now: Optional[float] = None,
) -> list[DeviceConfig]:
    """被收起的那些设备，用于在页脚提示"另有 N 台已断开"。"""
    minutes = effective_hide_minutes(prefs)
    if minutes <= 0:
        return []
    return [d for d in devices if d.enabled and not is_present(snapshots.get(d.id), now=now, hide_minutes=minutes)]
