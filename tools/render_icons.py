"""把自绘图标按钮放大渲染，用于人工核对与视觉回归。

用法：
    python tools/render_icons.py [输出路径]

放大采用 painter.scale()，也就是真正的矢量放大，不会像 grab() 那样糊。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from app.ui.glass import IconButton  # noqa: E402

KINDS = ("refresh", "sliders", "close", "back")
BUTTON_SIZE = 28
SCALE = 6
PAD = 14


def render_icon(kind: str, color: str, scale: int = SCALE, size: int = BUTTON_SIZE) -> QImage:
    button = IconButton(kind, diameter=size, color=color)
    button.ensurePolished()
    image = QImage(size * scale, size * scale, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.scale(scale, scale)
    button.render(painter, QPoint(0, 0), renderFlags=QWidget.RenderFlag.DrawChildren)
    painter.end()
    return image


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "preview" / "icons.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])  # noqa: F841

    cell = BUTTON_SIZE * SCALE
    width = PAD + len(KINDS) * (cell + PAD)
    height = PAD + 2 * (cell + PAD)
    canvas = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.fillRect(0, 0, width, height // 2, QColor("#F3F5F9"))
    painter.fillRect(0, height // 2, width, height - height // 2, QColor("#23262C"))

    for row, color in enumerate(("#5B5F6B", "#EDEEF2")):
        for index, kind in enumerate(KINDS):
            icon = render_icon(kind, color)
            painter.drawImage(PAD + index * (cell + PAD), PAD + row * (cell + PAD), icon)
    painter.end()

    canvas.save(str(out))
    print(f"saved {out}  ({width}x{height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
