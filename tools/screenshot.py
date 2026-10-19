"""界面预览截图（开发辅助）：把面板合成到一张柔和壁纸上，导出 PNG。

用法： .venv\\Scripts\\python.exe tools\\screenshot.py [输出目录]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QApplication, QScrollArea

from app.core.config import DeviceRegistry, SettingsStore
from app.core.monitor import Monitor
from app.ui import theme
from app.ui.panel import Panel
from app.ui.tray import TrayController


def make_wallpaper(width: int, height: int) -> QPixmap:
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#E8ECF4"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    base = QLinearGradient(0, 0, width, height)
    base.setColorAt(0.0, QColor("#EEF1F8"))
    base.setColorAt(0.5, QColor("#DFE6F2"))
    base.setColorAt(1.0, QColor("#E9E3F1"))
    painter.fillRect(0, 0, width, height, base)

    blobs = [
        (QPointF(width * 0.18, height * 0.22), width * 0.42, QColor(120, 160, 255, 150)),
        (QPointF(width * 0.86, height * 0.12), width * 0.34, QColor(255, 190, 150, 130)),
        (QPointF(width * 0.72, height * 0.86), width * 0.46, QColor(150, 220, 205, 130)),
        (QPointF(width * 0.08, height * 0.92), width * 0.30, QColor(200, 170, 255, 120)),
    ]
    for center, radius, color in blobs:
        radial = QRadialGradient(center, radius)
        radial.setColorAt(0.0, color)
        radial.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
        painter.setBrush(radial)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, radius, radius)
    painter.end()
    return QPixmap.fromImage(image)


def blurred_shadow(width: int, height: int, radius: float, blur: int, color: QColor) -> QImage:
    """缩到 1/8 画阴影形状再放大，得到平滑的高斯近似（形状四周留出 blur 边距）。"""
    scale = 0.125
    target_w, target_h = width + blur * 2, height + blur * 2
    small = QImage(max(4, int(target_w * scale)), max(4, int(target_h * scale)), QImage.Format.Format_ARGB32_Premultiplied)
    small.fill(Qt.GlobalColor.transparent)
    painter = QPainter(small)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(
        QRectF(blur * scale, blur * scale, width * scale, height * scale),
        radius * scale,
        radius * scale,
    )
    painter.end()
    return small.scaled(
        target_w,
        target_h,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def composite(panel: Panel, out_path: Path, extra_w: int = 408, extra_h: int = 180) -> None:
    # 先抓图再按真实尺寸生成壁纸：设置页会把窗口拉高，事先算好的尺寸会对不齐
    shot = panel.grab()
    wallpaper = make_wallpaper(shot.width() + extra_w, shot.height() + extra_h)
    canvas = wallpaper.copy()
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    x = (canvas.width() - shot.width()) / 2
    y = (canvas.height() - shot.height()) / 2
    pad = 26
    shadow = blurred_shadow(
        shot.width() - 2 * pad,
        shot.height() - 2 * pad,
        28,
        52,
        QColor(22, 30, 52, 78),
    )
    painter.drawImage(QPoint(int(x - 52), int(y - 52 + 16)), shadow)
    painter.drawPixmap(QPoint(int(x), int(y)), shot)
    painter.end()
    canvas.save(str(out_path), "PNG")
    print("saved", out_path, shot.width(), "x", shot.height())


def main() -> int:
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    live = "--live" in sys.argv
    out_dir = Path(argv[0]) if argv else Path("preview")
    out_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setFont(theme.font(12))
    app.setStyleSheet(theme.app_stylesheet())

    # 预览图必须可复现：用临时用户目录，既不受本机增删设备影响，也不会写坏真实配置
    import tempfile

    sandbox = Path(tempfile.mkdtemp(prefix="batterygass-shot-"))
    registry = DeviceRegistry(user_dir=sandbox)
    store = SettingsStore(registry, path=sandbox / "preferences.json")
    store.prefs.demo = not live
    monitor = Monitor(registry, store.prefs, demo=not live)
    tray = TrayController(registry, store)
    panel = Panel(registry, store, monitor, tray)

    # 用固定的演示电量，方便反复对比视觉
    from app.core.model import Snapshot

    if live:
        panel.show_panel()
        monitor.updated.connect(panel.apply_snapshots)
        QTimer.singleShot(200, monitor.scan_once)
    else:
        demo_levels = {"logitech-gpx-superlight": 87, "flydigi-apex4": 76, "mchose-g98pro": 64, "edifier-g3max": 92}
        charging = {"edifier-g3max": True}
        snaps = {}
        for cfg in registry.enabled_devices():
            level = demo_levels.get(cfg.id, 70)
            snaps[cfg.id] = Snapshot(
                device_id=cfg.id,
                level=level,
                charging=charging.get(cfg.id, False),
                online=True,
                source="demo",
                detail="演示数据",
                updated_at=__import__("time").time(),
                history=[level] * 8,
            )
        panel.apply_snapshots(snaps)
        panel.show_panel()

    suffix = "" if live else "-demo"

    def shoot_main() -> None:
        composite(panel, out_dir / f"panel{suffix}.png")
        panel._toggle_settings()
        QTimer.singleShot(700, shoot_settings)

    def shoot_settings() -> None:
        # 设置页比主面板高得多，且默认可能因为焦点落到下方控件而自动滚动，
        # 截图前先回到顶部，保证预览图总是同一副样子
        for area in panel.settings.findChildren(QScrollArea):
            area.verticalScrollBar().setValue(0)
        app.processEvents()
        composite(panel, out_dir / f"settings{suffix}.png", extra_w=288, extra_h=180)
        app.quit()

    QTimer.singleShot(3000 if live else 900, shoot_main)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
