"""一键自检：环境、配置、协议、读数全链路，不弹界面。

    .venv\\Scripts\\python.exe tools\\selfcheck.py

出问题时把这里的输出发出来，比"打不开"有用得多。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import autostart, xinput
from app.core.config import DeviceRegistry, SettingsStore
from app.core.hidapi_io import HIDAPI_AVAILABLE, HIDAPI_ERROR, enumerate_devices
from app.core.hidnative import _AVAILABLE as HIDNATIVE_AVAILABLE
from app.core.monitor import Monitor
from app.core.paths import log_path, user_data_dir
from app.core.protocols import read_device


def main() -> int:
    print("=" * 74)
    print("环境")
    print(f"  Python           : {sys.version.split()[0]}")
    print(f"  hidapi           : {'可用' if HIDAPI_AVAILABLE else '缺失 - ' + HIDAPI_ERROR}")
    print(f"  hid.dll (HidP)   : {'可用' if HIDNATIVE_AVAILABLE else '不可用'}")
    print(f"  XInput           : {'可用' if xinput.connected_slots() or xinput._library() else '不可用'}")
    print(f"  用户数据目录     : {user_data_dir()}")
    print(f"  日志             : {log_path()}")
    print(f"  开机自启         : {'已开启' if autostart.is_enabled() else '未开启'}")

    registry = DeviceRegistry()
    store = SettingsStore(registry)
    print("\n配置")
    print(f"  类型数量         : {len(registry.types)}  ({', '.join(registry.types)})")
    print(f"  设备数量         : {len(registry.devices)}")
    print(f"  演示模式         : {store.prefs.demo}")
    print(f"  刷新间隔         : {store.prefs.refresh_seconds}s")

    infos = enumerate_devices()
    print(f"\nHID 接口         : {len(infos)} 个")
    print("\n逐台读数")
    print("-" * 74)
    used: set[int] = set()
    ok_count = 0
    for cfg in registry.enabled_devices():
        reading = read_device(cfg, infos, used)
        flag = "✔" if reading.ok else ("…" if reading.sleeping else "✘")
        if reading.ok:
            ok_count += 1
        print(f"  {flag} {cfg.display_name:<14} {cfg.protocol_name:<15} "
              f"level={reading.level} charging={reading.charging} source={reading.source}")
        print(f"      └ {reading.detail}")
    print("-" * 74)
    print(f"可读数设备       : {ok_count}/{len(registry.enabled_devices())}")

    # 走一遍监测线程的合并逻辑（不启动线程）
    monitor = Monitor(registry, store.prefs, demo=store.prefs.demo)
    monitor.scan_once()
    snaps = monitor.snapshots()
    print("\n快照（UI 拿到的最终状态）")
    for device_id, snap in snaps.items():
        print(f"  {device_id:<26} level={snap.level} online={snap.online} "
              f"sleeping={snap.sleeping} status={snap.status_text(store.prefs.low_battery_threshold)}")
    print("\n自检完成。若某台设备显示 ✘，先用 tools\\usb_probe.py list 确认 VID/PID 是否匹配。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
