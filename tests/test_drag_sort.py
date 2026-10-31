"""拖拽排序逻辑测试（不涉及真实鼠标：直接喂落点坐标）。"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QWidget

from app.ui.drag_sort import ReorderController

ROW_W, ROW_H, COL_W = 200, 60, 200


def _build(qapp, count: int, *, columns: int = 1):
    """造一个 host，里面按显示顺序摆好 count 个行/卡片。"""
    host = QWidget()
    host.resize(COL_W * columns, ROW_H * ((count + columns - 1) // columns))
    rows = []
    keys = {}
    for index in range(count):
        row = QWidget(host)
        if columns == 1:
            row.setGeometry(0, index * ROW_H, ROW_W, ROW_H)
        else:
            row.setGeometry((index % columns) * COL_W, (index // columns) * ROW_H, COL_W - 10, ROW_H - 10)
        rows.append(row)
        keys[row] = f"k{index}"
    host.show()
    for row in rows:
        row.show()
    controller = ReorderController(host, key_of=lambda w: keys[w], columns=columns)
    controller.set_rows(rows)
    return host, rows, controller


@pytest.fixture()
def vertical(qapp):
    host, rows, controller = _build(qapp, 3)
    return host, rows, controller


def test_drag_first_to_end_reorders(qapp, vertical):
    host, rows, controller = vertical
    seen: list[list[str]] = []
    controller.reordered.connect(seen.append)

    controller.begin(rows[0])
    controller.drag(host.mapToGlobal(QPoint(10, 3 * ROW_H + 5)))
    controller.drop()

    assert seen == [["k1", "k2", "k0"]]


def test_drag_last_to_top_reorders(qapp, vertical):
    host, rows, controller = vertical
    seen: list[list[str]] = []
    controller.reordered.connect(seen.append)

    controller.begin(rows[2])
    controller.drag(host.mapToGlobal(QPoint(10, 0)))
    controller.drop()

    assert seen == [["k2", "k0", "k1"]]


def test_drop_in_place_is_noop(qapp, vertical):
    host, rows, controller = vertical
    seen: list[list[str]] = []
    controller.reordered.connect(seen.append)

    controller.begin(rows[1])
    controller.drag(host.mapToGlobal(QPoint(10, ROW_H + 5)))  # 仍在自己范围内
    controller.drop()

    assert seen == []


def test_drop_just_after_itself_is_noop(qapp, vertical):
    """插入点落在自己后面一位，顺序其实没变，不该写盘。"""
    host, rows, controller = vertical
    seen: list[list[str]] = []
    controller.reordered.connect(seen.append)

    controller.begin(rows[0])
    controller.drag(host.mapToGlobal(QPoint(10, ROW_H - 1)))
    controller.drop()

    assert seen == []


def test_marker_shows_while_dragging(qapp, vertical):
    host, rows, controller = vertical
    controller.begin(rows[0])
    assert controller.dragging is True
    controller.drag(host.mapToGlobal(QPoint(10, 3 * ROW_H + 5)))
    assert controller._marker.isVisible() is True
    controller.drop()
    assert controller._marker.isVisible() is False
    assert controller.dragging is False


def test_grid_two_columns_reorders(qapp):
    host, rows, controller = _build(qapp, 4, columns=2)
    seen: list[list[str]] = []
    controller.reordered.connect(seen.append)

    # 把第 1 张拖到第 4 格的右半边 → 落在末尾
    controller.begin(rows[0])
    controller.drag(host.mapToGlobal(QPoint(COL_W + COL_W // 2 + 20, ROW_H + 20)))
    controller.drop()

    assert seen == [["k1", "k2", "k3", "k0"]]


def test_grid_left_half_inserts_before(qapp):
    host, rows, controller = _build(qapp, 4, columns=2)
    seen: list[list[str]] = []
    controller.reordered.connect(seen.append)

    # 把第 3 张拖到第 1 格左半边 → 插到最前
    controller.begin(rows[2])
    controller.drag(host.mapToGlobal(QPoint(5, 5)))
    controller.drop()

    assert seen == [["k2", "k0", "k1", "k3"]]


def test_unknown_row_ignored(qapp, vertical):
    host, rows, controller = vertical
    controller.begin(QWidget())          # 不在列表里
    assert controller.dragging is False
    controller.drop()                    # 不应抛异常
