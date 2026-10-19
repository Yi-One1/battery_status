"""断连判定与自动收起测试（问题3 的"自动删除断连设备"）。

规则本身是纯函数，所以这里不需要任何硬件：喂 Snapshot 和时间戳就行。
"""
from __future__ import annotations

import time

import pytest

from app.core.config import DeviceConfig, DeviceRegistry, Preferences
from app.core.model import Snapshot
from app.core.presence import (
    effective_hide_minutes,
    filter_visible,
    hidden_devices,
    is_present,
    offline_seconds,
)

NOW = 1_700_000_000.0


def device(device_id: str, enabled: bool = True) -> DeviceConfig:
    return DeviceConfig(id=device_id, type="mouse", display_name=device_id, enabled=enabled)


def snap(device_id: str, *, online: bool, last_seen: float, level: int | None = 50, sleeping: bool = False) -> Snapshot:
    return Snapshot(
        device_id=device_id,
        level=level,
        online=online,
        updated_at=last_seen,
        last_seen=last_seen,
        sleeping=sleeping,
    )


@pytest.fixture()
def prefs():
    return Preferences()


# --------------------------------------------------------------------------- #
# 基础判定
# --------------------------------------------------------------------------- #
def test_offline_seconds_from_last_seen():
    assert offline_seconds(snap("a", online=False, last_seen=NOW - 120), NOW) == pytest.approx(120)


def test_offline_seconds_never_seen_is_infinite():
    assert offline_seconds(Snapshot(device_id="a"), NOW) == float("inf")


def test_online_device_always_present():
    old = snap("a", online=True, last_seen=NOW - 10 * 3600)
    assert is_present(old, now=NOW, hide_minutes=10) is True


def test_sleeping_device_always_present():
    """休眠的鼠标仍然是连着的那只鼠标，收起来用户就找不到它了。"""
    asleep = snap("a", online=True, last_seen=NOW - 10 * 3600, sleeping=True)
    assert is_present(asleep, now=NOW, hide_minutes=10) is True


def test_missing_snapshot_is_kept():
    assert is_present(None, now=NOW, hide_minutes=10) is True


def test_offline_within_window_is_kept():
    assert is_present(snap("a", online=False, last_seen=NOW - 300), now=NOW, hide_minutes=10) is True


def test_offline_beyond_window_is_hidden():
    assert is_present(snap("a", online=False, last_seen=NOW - 601), now=NOW, hide_minutes=10) is False


def test_boundary_hides_exactly_at_window():
    """窗口是"超过即收起"：599 秒还留着，到 600 秒整就收起。"""
    assert is_present(snap("a", online=False, last_seen=NOW - 599), now=NOW, hide_minutes=10) is True
    assert is_present(snap("a", online=False, last_seen=NOW - 600), now=NOW, hide_minutes=10) is False
    assert is_present(snap("a", online=False, last_seen=NOW - 601), now=NOW, hide_minutes=10) is False


def test_zero_minutes_disables_hiding():
    ancient = snap("a", online=False, last_seen=NOW - 30 * 86400)
    assert is_present(ancient, now=NOW, hide_minutes=0) is True


# --------------------------------------------------------------------------- #
# 集合过滤
# --------------------------------------------------------------------------- #
def test_filter_visible_mixed(prefs):
    prefs.offline_hide_minutes = 10
    devices = [device("live"), device("recent"), device("stale"), device("never")]
    snapshots = {
        "live": snap("live", online=True, last_seen=NOW - 9999),
        "recent": snap("recent", online=False, last_seen=NOW - 60),
        "stale": snap("stale", online=False, last_seen=NOW - 3600),
        # never 没有快照
    }
    visible = [d.id for d in filter_visible(devices, snapshots, prefs, now=NOW)]
    assert visible == ["live", "recent", "never"]
    hidden = [d.id for d in hidden_devices(devices, snapshots, prefs, now=NOW)]
    assert hidden == ["stale"]


def test_filter_visible_skips_disabled(prefs):
    devices = [device("on"), device("off", enabled=False)]
    snapshots = {"on": snap("on", online=True, last_seen=NOW), "off": snap("off", online=True, last_seen=NOW)}
    assert [d.id for d in filter_visible(devices, snapshots, prefs, now=NOW)] == ["on"]


def test_auto_hide_toggle_off_keeps_everything(prefs):
    prefs.auto_hide_offline = False
    devices = [device("stale")]
    snapshots = {"stale": snap("stale", online=False, last_seen=NOW - 99999)}
    assert filter_visible(devices, snapshots, prefs, now=NOW) == devices
    assert hidden_devices(devices, snapshots, prefs, now=NOW) == []
    assert effective_hide_minutes(prefs) == 0


def test_effective_hide_minutes_follows_prefs(prefs):
    prefs.offline_hide_minutes = 30
    assert effective_hide_minutes(prefs) == 30
    prefs.auto_hide_offline = False
    assert effective_hide_minutes(prefs) == 0


# --------------------------------------------------------------------------- #
# 与监测线程的衔接：last_seen 必须只在"看见"时前进
# --------------------------------------------------------------------------- #
def test_monitor_touch_tracks_last_seen(qapp, tmp_path):
    from app.core.monitor import Monitor
    from app.core.model import Reading

    registry = DeviceRegistry(user_dir=tmp_path)
    cfg = registry.devices[0]
    monitor = Monitor(registry, Preferences())

    # 第一次扫描：从当前时间起算基线
    ok = Reading(level=80, ok=True, source="demo")
    first = monitor._touch(cfg, ok, NOW)
    assert first == NOW

    # 读不到、也没检测到 → 时间不前进
    stale = monitor._touch(cfg, Reading.fail("未检测到设备"), NOW + 500)
    assert stale == NOW

    # 检测到设备（哪怕读不出电量）→ 前进
    detected = monitor._touch(cfg, Reading(detail="协议未配置", detected=True), NOW + 900)
    assert detected == NOW + 900

    # 休眠也算在
    asleep = monitor._touch(cfg, Reading.fail("休眠", sleeping=True), NOW + 1200)
    assert asleep == NOW + 1200


def test_monitor_prune_seen_drops_removed_devices(qapp, tmp_path):
    from app.core.monitor import Monitor

    registry = DeviceRegistry(user_dir=tmp_path)
    monitor = Monitor(registry, Preferences())
    monitor._last_seen["gone"] = time.time()
    monitor._prune_seen({})
    assert "gone" not in monitor._last_seen
