"""监测线程的合并逻辑（`Monitor._merge`）测试。

重点是"确定结论"和"一次没读到"必须区别对待：
  * 读失败要连续两次才置为离线（避免一次抖动就把卡片打灰）；
  * 但**设备明确关机**（漫步者关机后 dongle 仍回一帧 0%）是确定结论，
    必须立刻显示未连接，不能等到去抖结束、更不能显示成「电量偏低 0%」。
"""
from __future__ import annotations

import pytest

from app.core.config import DeviceConfig, DeviceRegistry, Preferences
from app.core.model import Reading
from app.core.monitor import Monitor


@pytest.fixture()
def monitor(qapp, tmp_path) -> Monitor:
    return Monitor(DeviceRegistry(user_dir=tmp_path), Preferences())


def _cfg() -> DeviceConfig:
    return DeviceConfig(
        id="edifier-g3max",
        type="headset",
        display_name="漫步者 G3 MAX",
        match={"vid": 0x35BB},
        protocol={"name": "vendor_query", "command": "D0 {vid_be} 01 00"},
    )


def test_successful_reading_keeps_the_card_online(monitor):
    snapshot = monitor._merge(_cfg(), Reading(level=48, ok=True, source="vendor-query"))
    assert snapshot.online is True
    assert snapshot.level == 48
    assert snapshot.status_text() == "使用中"


def test_powered_off_device_is_disconnected_immediately(monitor):
    """问题5 的正例：关机电量位是 0，但不能当成「只剩 0%」。"""
    cfg = _cfg()
    first = monitor._merge(cfg, Reading(level=48, ok=True, source="vendor-query"))
    monitor._snapshots[cfg.id] = first

    snapshot = monitor._merge(cfg, Reading(offline=True, detail="设备已关闭"))

    assert snapshot.online is False
    assert snapshot.level is None
    assert snapshot.charging is None
    assert snapshot.status_text() == "未连接"
    assert "已关闭" in snapshot.detail


def test_powered_off_clears_the_history(monitor):
    cfg = _cfg()
    for level in (60, 52):
        monitor._snapshots[cfg.id] = monitor._merge(
            cfg, Reading(level=level, ok=True, source="vendor-query")
        )
    assert monitor._merge(cfg, Reading(offline=True)).history == []


def test_offline_does_not_refresh_last_seen(monitor):
    """关机的设备不该被当成"刚看见" —— 否则断连自动收起永远不会触发。"""
    cfg = _cfg()
    monitor._last_seen[cfg.id] = 1000.0
    monitor._merge(cfg, Reading(offline=True))
    assert monitor._last_seen[cfg.id] == 1000.0


def test_success_refreshes_last_seen(monitor):
    cfg = _cfg()
    monitor._last_seen[cfg.id] = 1000.0
    snapshot = monitor._merge(cfg, Reading(level=48, ok=True, source="vendor-query"))
    assert monitor._last_seen[cfg.id] == pytest.approx(snapshot.last_seen)
    assert monitor._last_seen[cfg.id] > 1000.0


def test_single_failure_keeps_the_last_value(monitor):
    """一次读失败不代表设备掉了，保留上一次的电量，卡片不打灰。"""
    cfg = _cfg()
    monitor._snapshots[cfg.id] = monitor._merge(cfg, Reading(level=48, ok=True, source="vendor-query"))
    snapshot = monitor._merge(cfg, Reading.fail("没应答"))

    assert snapshot.online is True
    assert snapshot.level == 48


def test_two_failures_mark_it_offline(monitor):
    cfg = _cfg()
    monitor._snapshots[cfg.id] = monitor._merge(cfg, Reading(level=48, ok=True, source="vendor-query"))
    monitor._merge(cfg, Reading.fail("没应答"))
    snapshot = monitor._merge(cfg, Reading.fail("没应答"))

    assert snapshot.online is False


def test_detected_device_stays_online_without_a_number(monitor):
    """设备在、但协议读不到电量：算在线，只是没有数值。"""
    snapshot = monitor._merge(_cfg(), Reading(detected=True, detail="该 dongle 不暴露电量页"))
    assert snapshot.online is True
    assert snapshot.level is None
    assert snapshot.status_text() == "暂无电量"
