"""玻璃质感控件渲染测试。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module", autouse=True)
def _ensure_qapp():
    if QApplication.instance() is None:
        QApplication(["test"])


def test_icon_button_kinds_render_without_crash(monkeypatch):
    """所有图标按钮（刷新、设置、关闭）都应能正常绘制，不抛异常。"""
    from app.ui.glass import IconButton

    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)

    for kind in ("refresh", "gear", "close"):
        btn = IconButton(kind, None, diameter=28)
        painter = QPainter(pixmap)
        btn.paintEvent(None)
        painter.end()
