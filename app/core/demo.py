"""演示数据源：仅用于预览界面 / 未接硬件时验证链路，UI 会标注「演示」。"""
from __future__ import annotations

import random
from dataclasses import dataclass

from .config import DeviceConfig
from .model import Reading


@dataclass
class DemoSource:
    level: float
    charging: bool = False
    tick: int = 0

    @classmethod
    def from_config(cls, cfg: DeviceConfig) -> "DemoSource":
        return cls(level=float(cfg.demo_level))

    def read(self) -> Reading:
        self.tick += 1
        if self.charging:
            self.level = min(100.0, self.level + 0.9)
            if self.level >= 100:
                self.charging = False
        else:
            self.level = max(0.0, self.level - random.uniform(0.15, 0.6))
            if self.level <= 12 and random.random() < 0.04:
                self.charging = True
        return Reading(
            level=int(round(self.level)),
            charging=self.charging,
            source="demo",
            ok=True,
            detail="演示数据",
        )
