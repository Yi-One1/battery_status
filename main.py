"""设备电量显示 · 入口。

用法：
    python main.py            # 启动并弹出面板
    python main.py --tray     # 直接进托盘（开机自启用这个参数）
    python main.py --demo     # 演示数据，先把界面点亮
"""
from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from app.core.config import DeviceRegistry, SettingsStore
from app.core.monitor import Monitor
from app.core.paths import APP_DISPLAY_NAME, log_path, resource_root
from app.ui import theme
from app.ui.panel import Panel
from app.ui.tray import TrayController, battery_icon

SINGLE_INSTANCE_KEY = "PeripheralBatteryPanel"


def setup_logging(verbose: bool = False) -> None:
    handlers: list[logging.Handler] = []
    try:
        handlers.append(
            RotatingFileHandler(log_path(), maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
        )
    except Exception:  # noqa: BLE001
        pass
    if verbose:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers or [logging.NullHandler()],
    )


def acquire_single_instance(app: QApplication, on_activate) -> tuple[bool, QLocalServer]:
    server = QLocalServer()
    if server.listen(SINGLE_INSTANCE_KEY):
        server.newConnection.connect(on_activate)
        return True, server
    socket = QLocalSocket()
    socket.connectToServer(SINGLE_INSTANCE_KEY)
    if socket.waitForConnected(400):
        socket.write(b"show")
        socket.flush()
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()
    return False, server


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_DISPLAY_NAME)
    parser.add_argument("--tray", action="store_true", help="启动后直接进入托盘，不显示面板")
    parser.add_argument("--demo", action="store_true", help="使用演示数据启动")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args()

    setup_logging(args.verbose)
    log = logging.getLogger("main")
    log.info("==== %s 启动 ====", APP_DISPLAY_NAME)

    app = QApplication(sys.argv)
    app.setApplicationName("PeripheralBattery")
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setFont(theme.font(12))
    app.setStyleSheet(theme.app_stylesheet())

    registry = DeviceRegistry()
    store = SettingsStore(registry)
    if args.demo:
        store.prefs.demo = True
    demo = store.prefs.demo or args.demo or not registry.enabled_devices()

    monitor = Monitor(registry, store.prefs, demo=demo)
    tray = TrayController(registry, store)
    panel = Panel(registry, store, monitor, tray)

    icon_path = resource_root() / "app" / "assets" / "app.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    else:
        app.setWindowIcon(battery_icon(88, theme.ACCENT_BLUE))

    def on_activate() -> None:
        while True:
            connection = server.nextPendingConnection()
            if connection is None:
                break
            connection.readyRead.connect(lambda c=connection: c.readAll())
            connection.disconnected.connect(connection.deleteLater)
            panel.show_panel()
            break

    ok, server = acquire_single_instance(app, on_activate)
    if not ok:
        log.info("已存在实例，唤起面板后退出")
        return 0

    def on_updated(snapshots: dict) -> None:
        panel.apply_snapshots(snapshots)
        tray.update_snapshots(snapshots)

    monitor.updated.connect(on_updated)
    monitor.scan_started.connect(lambda: None)

    tray.show_panel.connect(panel.show_panel)
    tray.toggle_requested.connect(panel.toggle_panel)
    tray.show_settings.connect(panel.open_settings)
    tray.refresh_requested.connect(panel.show_panel)
    tray.quit_requested.connect(app.quit)

    tray.update_snapshots({})
    monitor.start()

    start_hidden = args.tray or (store.prefs.start_minimized and store.prefs.autostart)
    if start_hidden:
        QTimer.singleShot(300, lambda: tray.notify("设备电量显示已在托盘运行", "左键单击托盘图标即可查看各设备电量。"))
    else:
        QTimer.singleShot(120, panel.show_panel)

    def shutdown() -> None:
        log.info("退出中…")
        monitor.stop()
        monitor.wait(1500)
        tray.shutdown()

    app.aboutToQuit.connect(shutdown)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
