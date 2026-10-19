"""后台监测线程：定时扫描、失败重试、离线判定、历史曲线。

设计要点（低占用）：
  * 单线程串行扫描，默认 60s 一次，扫描完就 Event.wait 休眠，不占 CPU
  * 只打开命中配置的设备句柄，读完立刻关闭，不长期占用 USB 接口
  * HID++ 接收器的索引探测结果带 5 分钟缓存，避免每轮都全索引试探
  * 手动刷新 / 设备插拔时用 Event 立即唤醒，不做忙轮询
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Dict, Optional

from PySide6.QtCore import QThread, Signal

from .config import DeviceConfig, DeviceRegistry, Preferences
from .demo import DemoSource
from .discovery import available_devices
from .hidapi_io import HIDAPI_AVAILABLE, enumerate_devices
from .model import Reading, Snapshot
from .protocols import candidate_infos, read_device

log = logging.getLogger(__name__)

HISTORY_POINTS = 20


class Monitor(QThread):
    scan_started = Signal()
    updated = Signal(dict)  # device_id -> Snapshot
    # 扫到"在用但没登记"的设备时上报，交给界面线程落盘（配置写入必须在 UI 线程做）
    discovered = Signal(list)

    def __init__(self, registry: DeviceRegistry, prefs: Preferences, demo: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.registry = registry
        self.prefs = prefs
        self.demo = demo or prefs.demo
        self._stop_event = threading.Event()
        self._wake = threading.Event()
        self._snapshots: Dict[str, Snapshot] = {}
        self._misses: Dict[str, int] = {}
        self._history: Dict[str, deque[int]] = {}
        self._demos: Dict[str, DemoSource] = {}
        self._last_seen: Dict[str, float] = {}

    # -- 对外控制 -----------------------------------------------------------
    def stop(self) -> None:
        self._stop_event.set()
        self._wake.set()

    def refresh(self) -> None:
        self._wake.set()

    def set_prefs(self, prefs: Preferences) -> None:
        self.prefs = prefs
        self.demo = prefs.demo
        self._wake.set()

    def snapshots(self) -> Dict[str, Snapshot]:
        return dict(self._snapshots)

    # -- 线程主体 -----------------------------------------------------------
    def run(self) -> None:  # noqa: D401
        log.info("监测线程启动（demo=%s, hidapi=%s）", self.demo, HIDAPI_AVAILABLE)
        while not self._stop_event.is_set():
            try:
                self.scan_once()
            except Exception as exc:  # noqa: BLE001
                log.exception("扫描异常：%s", exc)
            interval = max(10, int(self.prefs.refresh_seconds))
            self._wake.wait(interval)
            self._wake.clear()
        log.info("监测线程退出")

    # -- 单轮扫描 -----------------------------------------------------------
    def scan_once(self) -> None:
        self.scan_started.emit()
        started = time.time()
        infos = [] if self.demo else enumerate_devices()
        result: Dict[str, Snapshot] = {}

        used_slots: set[int] = set()
        for cfg in self.registry.enabled_devices():
            reading = self._read_one(cfg, infos, used_slots)
            result[cfg.id] = self._merge(cfg, reading)
        self._prune_seen(result)

        self._snapshots = result
        elapsed = (time.time() - started) * 1000
        resolved = sum(1 for s in result.values() if s.level is not None and s.online)
        log.debug("扫描完成：%d/%d 台有数据，耗时 %.0f ms", resolved, len(result), elapsed)
        self.updated.emit(dict(result))
        self._publish_discovery(infos)

    def _prune_seen(self, result: Dict[str, Snapshot]) -> None:
        """设备被删掉后，它的"最后出现时间"没必要一直留着。"""
        for device_id in list(self._last_seen):
            if device_id not in result:
                self._last_seen.pop(device_id, None)

    def _touch(self, cfg: DeviceConfig, reading: Reading, now: float) -> float:
        """维护 last_seen：读成功 / 设备在但读不到 / 休眠 都算"看见"。

        第一次遇到某台设备时以当前时间做基线 —— 也就是说，一台从来没插过的设备
        会从程序启动开始计时，而不是被当成"离线了 50 年"立刻消失。
        """
        seen = self._last_seen.get(cfg.id)
        if seen is None:
            seen = now
        if reading.ok or reading.detected or reading.sleeping:
            seen = now
        self._last_seen[cfg.id] = seen
        return seen

    def _publish_discovery(self, infos: list) -> None:
        """把新设备交给界面线程登记（配置写入只允许在 UI 线程发生）。"""
        if self.demo or not infos or not self.prefs.auto_add:
            return
        try:
            found = available_devices(infos, self.registry)
        except Exception as exc:  # noqa: BLE001
            log.debug("设备发现失败：%s", exc)
            return
        if found:
            log.info("发现未登记设备：%s", ", ".join(d.key for d in found))
            self.discovered.emit(found)

    def _read_one(self, cfg: DeviceConfig, infos: list, used_slots: set[int]) -> Reading:
        if self.demo or cfg.protocol_name == "demo":
            source = self._demos.get(cfg.id)
            if source is None:
                source = DemoSource.from_config(cfg)
                self._demos[cfg.id] = source
            return source.read()

        if cfg.protocol_name != "xinput" and not HIDAPI_AVAILABLE:
            return Reading.fail("未安装 hidapi，无法访问 USB HID")

        if cfg.protocol_name != "xinput" and not candidate_infos(infos, cfg):
            return Reading.fail("未检测到设备（检查 2.4G 接收器是否插入）")

        return read_device(cfg, infos, used_slots)

    def _merge(self, cfg: DeviceConfig, reading: Reading) -> Snapshot:
        previous = self._snapshots.get(cfg.id)
        history = self._history.setdefault(cfg.id, deque(maxlen=HISTORY_POINTS))
        now = time.time()
        seen = self._touch(cfg, reading, now)

        if reading.sleeping:
            self._misses[cfg.id] = 0
            return Snapshot(
                device_id=cfg.id,
                level=previous.level if previous else None,
                charging=previous.charging if previous else None,
                online=True,
                source=previous.source if previous else "none",
                detail=reading.detail,
                updated_at=previous.updated_at if previous else 0.0,
                history=list(history),
                sleeping=True,
                last_seen=seen,
                # 休眠不影响"走哪条链路"，沿用上一次的判断
                link=previous.link if previous else "",
            )

        if reading.ok:
            self._misses[cfg.id] = 0
            online = True
            level = reading.level
            if level is not None and (not history or history[-1] != level):
                history.append(level)
        elif reading.detected:
            # 设备在，但协议读不到电量：保持在线，只是没有数值
            self._misses[cfg.id] = 0
            history.clear()
            return Snapshot(
                device_id=cfg.id,
                level=None,
                charging=None,
                online=True,
                source="none",
                detail=reading.detail,
                updated_at=previous.updated_at if previous else 0.0,
                history=[],
                sleeping=False,
                last_seen=seen,
                link=reading.link or (previous.link if previous else ""),
            )
        elif reading.offline:
            # 明确判定为"设备已关闭"：立刻置为未连接，不走"连续两次失败"的去抖，
            # 因为这是确定结论，不是一次没读到。
            self._misses[cfg.id] = 0
            history.clear()
            return Snapshot(
                device_id=cfg.id,
                level=None,
                charging=None,
                online=False,
                source="none",
                detail=reading.detail,
                updated_at=previous.updated_at if previous else 0.0,
                history=[],
                sleeping=False,
                last_seen=seen,
            )
        else:
            misses = self._misses.get(cfg.id, 0) + 1
            self._misses[cfg.id] = misses
            online = misses < 2 and previous is not None and previous.level is not None
            level = previous.level if previous else None
            if misses >= 4:
                history.clear()

        return Snapshot(
            device_id=cfg.id,
            level=level,
            charging=reading.charging if reading.ok else (previous.charging if previous else None),
            online=online,
            source=reading.source if reading.ok else "none",
            detail=reading.detail or (previous.detail if previous else ""),
            updated_at=now if reading.ok else (previous.updated_at if previous else 0.0),
            history=list(history),
            sleeping=False,
            last_seen=seen,
            link=(reading.link if reading.ok else "") or (previous.link if previous else ""),
        )
