"""测试共用装置：离屏 Qt 环境 + 位图几何分析工具。"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def ink_points(image: QImage, threshold: int = 60) -> list[tuple[float, float]]:
    """返回所有"有墨迹"像素的中心坐标（以图像左上角为原点）。"""
    canvas = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = canvas.width(), canvas.height()
    stride = canvas.bytesPerLine()
    raw = bytes(canvas.constBits())
    points: list[tuple[float, float]] = []
    for y in range(height):
        row = raw[y * stride : y * stride + width * 4]
        for x in range(width):
            if row[x * 4 + 3] > threshold:
                points.append((x + 0.5, y + 0.5))
    return points


def radial_profile(
    points: list[tuple[float, float]],
    width: float,
    height: float,
    bins: int = 360,
) -> list[float]:
    """按角度统计每个方向上最远的墨迹半径，以短边的一半归一化。

    角度约定与 Qt 的 arcTo 一致：0° 指向右，逆时针为正。
    """
    profile = [0.0] * bins
    if not points:
        return profile
    cx, cy = width / 2.0, height / 2.0
    unit = min(width, height) / 2.0
    for px, py in points:
        dx, dy = px - cx, py - cy
        distance = math.hypot(dx, dy) / unit
        angle = math.degrees(math.atan2(-dy, dx)) % 360.0
        index = int(angle / (360.0 / bins)) % bins
        if distance > profile[index]:
            profile[index] = distance
    return profile


def bbox(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def band_coverage(profile: list[float], low: float = 0.55, high: float = 0.80) -> float:
    """落在环带内的角度占比（衡量"这一圈是否画圆了"）。"""
    hits = sum(1 for value in profile if low <= value <= high)
    return hits / max(1, len(profile))


def gap_angles(profile: list[float], low: float = 0.55) -> list[int]:
    """外轮廓上完全没有墨迹的角度。"""
    return [index for index, value in enumerate(profile) if value < low]


def longest_gap(profile: list[float], low: float = 0.55) -> tuple[float, float]:
    """返回最长的连续缺口区间（起始角, 宽度）。

    小尺寸图标受抗锯齿影响，个别角度会掉到阈值以下形成碎片，
    所以按"最长的连续段"来衡量缺口，而不是简单计数。
    """
    bins = len(profile)
    mask = [value < low for value in profile]
    if not any(mask):
        return (-1.0, 0.0)
    if all(mask):
        return (0.0, 360.0)
    # 从某个"有墨迹"的位置切开，避免缺口跨越 0°/360° 边界被截断
    start_index = mask.index(False)
    best_start, best_len = 0, 0
    run_start, run_len = None, 0
    for offset in range(1, bins + 1):
        index = (start_index + offset) % bins
        if mask[index]:
            if run_start is None:
                run_start = index
                run_len = 0
            run_len += 1
            if run_len > best_len:
                best_start, best_len = run_start, run_len
        else:
            run_start, run_len = None, 0
    return (best_start * 360.0 / bins, best_len * 360.0 / bins)


def quadrant_coverage(profile: list[float]) -> dict[str, float]:
    names = {"右上": (0, 90), "左上": (90, 180), "左下": (180, 270), "右下": (270, 360)}
    result = {}
    for name, (low, high) in names.items():
        segment = profile[low:high]
        hits = sum(1 for value in segment if 0.55 <= value <= 0.80)
        result[name] = hits / len(segment)
    return result
