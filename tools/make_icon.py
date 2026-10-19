"""生成应用图标（app.ico / app.png），供窗口、托盘与打包使用。

不依赖 Pillow：Qt 负责画图和 PNG 编码，ICO 容器手写（Vista 起支持 PNG 内嵌）。
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QRadialGradient
from PySide6.QtWidgets import QApplication

SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    scale = size / 256.0

    body = QRectF(0, 0, size, size)
    radius = 58 * scale
    path = QPainterPath()
    path.addRoundedRect(body, radius, radius)

    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor("#5AA9FF"))
    gradient.setColorAt(0.55, QColor("#3D7BFF"))
    gradient.setColorAt(1.0, QColor("#7C5CFF"))
    painter.fillPath(path, gradient)

    glow = QRadialGradient(QPointF(size * 0.26, size * 0.16), size * 0.8)
    glow.setColorAt(0.0, QColor(255, 255, 255, 120))
    glow.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillPath(path, glow)

    # 电池外壳
    shell = QRectF(size * 0.22, size * 0.33, size * 0.48, size * 0.34)
    nub = QRectF(shell.right() + size * 0.025, shell.top() + size * 0.10, size * 0.055, size * 0.14)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255, 210))
    painter.drawRoundedRect(nub, size * 0.02, size * 0.02)
    painter.setBrush(QColor(255, 255, 255, 70))
    painter.drawRoundedRect(shell, size * 0.075, size * 0.075)

    inner = shell.adjusted(size * 0.035, size * 0.035, -size * 0.035, -size * 0.035)
    fill = QRectF(inner.left(), inner.top(), inner.width() * 0.72, inner.height())
    painter.setBrush(QColor(255, 255, 255, 245))
    painter.drawRoundedRect(fill, size * 0.045, size * 0.045)

    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QColor(255, 255, 255, 230))
    painter.drawRoundedRect(shell, size * 0.075, size * 0.075)

    # 四角小圆点，暗示"多设备"
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255, 150))
    for dx, dy in ((0.33, 0.77), (0.43, 0.77), (0.53, 0.77)):
        painter.drawEllipse(QPointF(size * dx, size * dy), size * 0.022, size * 0.022)

    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    data = bytes(buffer.data())
    buffer.close()
    return data


def write_ico(images: list[QImage], target: Path) -> None:
    payloads = [png_bytes(image) for image in images]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = bytearray()
    for image, payload in zip(images, payloads):
        width = 0 if image.width() >= 256 else image.width()
        height = 0 if image.height() >= 256 else image.height()
        entries += struct.pack("<BBBBHHII", width, height, 0, 0, 1, 32, len(payload), offset)
        offset += len(payload)
    target.write_bytes(header + bytes(entries) + b"".join(payloads))


def main() -> int:
    app = QApplication(sys.argv)  # noqa: F841  QPainter/字体需要 QGuiApplication 环境
    out_dir = Path(__file__).resolve().parents[1] / "app" / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)

    images = [render(size) for size in SIZES]
    write_ico(images, out_dir / "app.ico")
    images[-1].save(str(out_dir / "app.png"), "PNG")
    print("已生成：", out_dir / "app.ico", out_dir / "app.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
