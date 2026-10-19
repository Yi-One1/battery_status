"""界面预览截图（开发辅助）：把面板合成到一张柔和壁纸上，导出 PNG。

用法： .venv\\Scripts\\python.exe tools\\screenshot.py [输出目录]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QApplication, QScrollArea

from app.core.config import DeviceRegistry, SettingsStore
from app.core.monitor import Monitor
from app.ui import theme
from app.ui.backdrop import analyse, blur_image
from app.ui.glass import set_backdrop
from app.ui.panel import Panel
from app.ui.tray import TrayController


# 三档壁纸：预览只用 mid，light / dark 用来验证"自适应通透度"有没有真的动起来
_WALLS: dict[str, tuple[str, list[tuple[float, float, float, str]]]] = {
    # tone: (底色, [(x, y, radius, color), ...])
    "light": (
        "#EEF1F8",
        [
            (0.18, 0.22, 0.42, "#9DBEF5"),
            (0.86, 0.12, 0.34, "#F5C79E"),
            (0.72, 0.86, 0.46, "#9EDCCB"),
            (0.08, 0.92, 0.30, "#C9B4F2"),
        ],
    ),
    "mid": (
        "#8FA9CC",
        [
            (0.16, 0.18, 0.40, "#4C6FB8"),
            (0.88, 0.10, 0.38, "#E39A62"),
            (0.76, 0.88, 0.44, "#3E9C8B"),
            (0.06, 0.94, 0.34, "#7B5FB0"),
            (0.50, 0.52, 0.30, "#C8D7EA"),
        ],
    ),
    "dark": (
        "#171A24",
        [
            (0.20, 0.16, 0.38, "#2D3E7A"),
            (0.86, 0.14, 0.34, "#8A4A2A"),
            (0.74, 0.88, 0.44, "#1B5E52"),
            (0.08, 0.90, 0.32, "#412E6B"),
        ],
    ),
}


def make_wallpaper(width: int, height: int, tone: str = "mid") -> QPixmap:
    base_color, blobs = _WALLS.get(tone, _WALLS["mid"])
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(base_color))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # 底色本身也给一点方向感，免得纯平
    base = QLinearGradient(0, 0, width, height)
    top = QColor(base_color)
    bottom = top.darker(118)
    base.setColorAt(0.0, top)
    base.setColorAt(1.0, bottom)
    painter.fillRect(0, 0, width, height, base)

    for fx, fy, fr, color in blobs:
        center = QPointF(width * fx, height * fy)
        radius = width * fr
        blob = QColor(color)
        radial = QRadialGradient(center, radius)
        radial.setColorAt(0.0, QColor(blob.red(), blob.green(), blob.blue(), 215))
        radial.setColorAt(0.55, QColor(blob.red(), blob.green(), blob.blue(), 120))
        radial.setColorAt(1.0, QColor(blob.red(), blob.green(), blob.blue(), 0))
        painter.setBrush(radial)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, radius, radius)
    painter.end()
    return QPixmap.fromImage(image)


def composite(panel: Panel, out_path: Path, extra_w: int = 408, extra_h: int = 180, tone: str = "mid") -> None:
    """把面板合成到柔和壁纸上。

    关键一步：**用同一张壁纸喂给玻璃材质**。面板是叠在壁纸上的，
    玻璃透出的必须是它背后那块壁纸，否则预览图会出现"玻璃里的桌面和背景不是一张图"。
    """
    size = panel.size()
    wallpaper = make_wallpaper(size.width() + extra_w, size.height() + extra_h, tone)
    x = (wallpaper.width() - size.width()) / 2
    y = (wallpaper.height() - size.height()) / 2

    behind = wallpaper.copy(QRect(int(x), int(y), size.width(), size.height())).toImage()
    behind.setDevicePixelRatio(1.0)
    # 底图按"屏幕全局坐标"对齐（玻璃现在用 mapToGlobal 取像素），
    # 所以这块壁纸要挂在面板真实的屏幕位置上，否则预览里的玻璃会错位。
    set_backdrop(
        QPixmap.fromImage(blur_image(behind)),
        analyse(behind),
        origin=panel.frameGeometry().topLeft(),
        scale=1.0,
    )

    shot = panel.grab()
    canvas = wallpaper.copy()
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # 阴影由 shell 自己的 QGraphicsDropShadowEffect 画在 MARGIN 留白里，
    # 这里再叠一层会变成双层阴影（边缘发脏），所以只贴图不补阴影。
    painter.drawPixmap(QPoint(int(x), int(y)), shot)
    painter.end()
    canvas.save(str(out_path), "PNG")
    print("saved", out_path, shot.width(), "x", shot.height())


def main() -> int:
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    live = "--live" in sys.argv
    tone = "mid"
    for flag in sys.argv[1:]:
        if flag.startswith("--wall="):
            tone = flag.split("=", 1)[1] or "mid"
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
    if tone != "mid":
        suffix = f"{suffix}-{tone}"

    def shoot_main() -> None:
        composite(panel, out_dir / f"panel{suffix}.png", tone=tone)
        panel._toggle_settings()
        QTimer.singleShot(700, shoot_settings)

    def shoot_settings() -> None:
        # 设置页比主面板高得多，且默认可能因为焦点落到下方控件而自动滚动，
        # 截图前先回到顶部，保证预览图总是同一副样子
        for area in panel.settings.findChildren(QScrollArea):
            area.verticalScrollBar().setValue(0)
        app.processEvents()
        composite(panel, out_dir / f"settings{suffix}.png", extra_w=288, extra_h=180, tone=tone)
        app.quit()

    QTimer.singleShot(3000 if live else 900, shoot_main)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
