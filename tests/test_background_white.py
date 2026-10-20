"""纯白色背景模式测试：整块不透明，与桌面彻底隔开。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from app.core.config import BACKGROUND_LIVE_GLASS, BACKGROUND_WHITE


pytestmark = pytest.mark.usefixtures("qapp")


def _render_solid(solid: bool) -> QImage:
    """把 paint_glass 画在红色底上，返回结果位图。"""
    from app.ui import glass

    glass.clear_backdrop()  # 排除背景板干扰
    image = QImage(80, 80, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(255, 0, 0))  # 红底：任何透明混入都会发粉
    painter = QPainter(image)
    glass.paint_glass(
        painter,
        QRectF(4, 4, 72, 72),
        18.0,
        alpha=0.86,
        solid=solid,
    )
    painter.end()
    return image


def test_solid_paint_glass_is_fully_opaque():
    """solid=True 时内部与边缘都是不透明白，红色底一点都不能透上来。"""
    image = _render_solid(solid=True)

    center = image.pixelColor(40, 40)
    assert center.alpha() == 255 and center.red() == 255, f"中心不白：{center.name()}"

    # 直边内侧（避开圆角）：老画法边缘只有 ~0.58 不透明度，会透出红底
    edge = image.pixelColor(8, 40)
    assert edge.alpha() == 255, f"边缘不透明度不足：{edge.name()}"
    assert edge.red() == 255 and edge.green() == 255 and edge.blue() == 255, f"边缘透出底色：{edge.name()}"

    # 圆角外仍是红底（没有把整个矩形涂满）
    outside = image.pixelColor(1, 1)
    assert outside.red() == 255 and outside.green() == 0, f"圆角外应为红底：{outside.name()}"


def test_glass_paint_glass_without_solid_keeps_translucent_edge():
    """对照：非 solid 的柔光玻璃在红底上边缘会透色（保持原设计不被误改）。"""
    image = _render_solid(solid=False)
    edge = image.pixelColor(8, 40)
    assert edge.green() > 0 or edge.alpha() < 255, "非 solid 模式边缘应当是半透明的玻璃质感"


def test_white_mode_shell_is_solid_and_opaque(qapp, tmp_path):
    """Panel 在纯白模式：外壳 solid=True 且 alpha=1.0；切回柔光玻璃恢复。"""
    from app.core.config import DeviceRegistry, SettingsStore
    from app.core.monitor import Monitor
    from app.ui.panel import Panel
    from app.ui.tray import TrayController

    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    store.prefs.background_mode = BACKGROUND_WHITE
    monitor = Monitor(registry, store.prefs)
    tray = TrayController(registry, store)

    panel = Panel(registry, store, monitor, tray)
    panel._apply_window_blur()
    assert panel.shell.solid is True
    assert panel.shell.base_alpha == 1.0

    store.prefs.background_mode = BACKGROUND_LIVE_GLASS
    panel._apply_window_blur()
    assert panel.shell.solid is False
    assert panel.shell.base_alpha < 1.0
