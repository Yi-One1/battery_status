"""视觉基调：颜色、字体、通用 QSS。

风格关键词：柔光玻璃（Light Glass）——
大面积低饱和白 + 极细高光描边 + 大圆角 + 柔和外阴影 + 类型色仅用于强调。
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase

TEXT_PRIMARY = "#1C1C1E"
TEXT_SECONDARY = "#5C5C61"
TEXT_TERTIARY = "#8E8E93"
DIVIDER = "rgba(60, 60, 67, 0.10)"
SURFACE = "rgba(255, 255, 255, 0.62)"
SURFACE_STRONG = "rgba(255, 255, 255, 0.86)"
BORDER_SOFT = "rgba(255, 255, 255, 0.75)"
ACCENT_BLUE = "#0A84FF"
WARN = "#FF9F0A"
DANGER = "#FF453A"
OK = "#2FC48A"

DISPLAY_FAMILIES = ["Segoe UI Variable Display", "Segoe UI", "Microsoft YaHei UI", "PingFang SC"]
TEXT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "PingFang SC"]
NUMBER_FAMILIES = ["Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI", "Microsoft YaHei UI"]


def _available(families: list[str]) -> list[str]:
    installed = set(QFontDatabase.families())
    return [f for f in families if f in installed] or ["Segoe UI"]


def font(size: float, weight: int = 400, families: list[str] | None = None, letter_spacing: float = 0.0) -> QFont:
    result = QFont()
    result.setFamilies(_available(families or TEXT_FAMILIES))
    result.setPixelSize(int(round(size)))
    result.setWeight(QFont.Weight(weight))
    if letter_spacing:
        result.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, letter_spacing)
    result.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return result


def color(value: str, alpha: float | None = None) -> QColor:
    c = QColor(value)
    if alpha is not None:
        c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def mix(a: str, b: str, ratio: float) -> QColor:
    ca, cb = QColor(a), QColor(b)
    ratio = max(0.0, min(1.0, ratio))
    return QColor(
        int(ca.red() + (cb.red() - ca.red()) * ratio),
        int(ca.green() + (cb.green() - ca.green()) * ratio),
        int(ca.blue() + (cb.blue() - ca.blue()) * ratio),
    )


def lighten(value: str, ratio: float = 0.25) -> QColor:
    return mix(value, "#FFFFFF", ratio)


def darken(value: str, ratio: float = 0.2) -> QColor:
    return mix(value, "#000000", ratio)


def app_stylesheet() -> str:
    """全局样式：菜单、工具提示、对话框、滚动条、滑块。"""
    return f"""
    QToolTip {{
        background-color: rgba(28, 28, 30, 0.92);
        color: #FFFFFF;
        border: none;
        border-radius: 8px;
        padding: 6px 10px;
        font-size: 12px;
    }}
    QMenu {{
        background-color: rgba(255, 255, 255, 0.96);
        border: 1px solid rgba(0, 0, 0, 0.08);
        border-radius: 12px;
        padding: 6px;
        color: {TEXT_PRIMARY};
    }}
    QMenu::item {{
        padding: 7px 18px 7px 14px;
        border-radius: 8px;
        font-size: 12px;
    }}
    QMenu::item:selected {{
        background-color: rgba(10, 132, 255, 0.12);
        color: {TEXT_PRIMARY};
    }}
    QMenu::separator {{
        height: 1px;
        background: {DIVIDER};
        margin: 5px 8px;
    }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 2px 0;
    }}
    QScrollBar::handle:vertical {{
        background: rgba(60, 60, 67, 0.26);
        border-radius: 4px;
        min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{ background: rgba(60, 60, 67, 0.30); }}
    QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
        height: 0; width: 0; background: transparent; border: none;
    }}
    QSlider::groove:horizontal {{
        height: 4px;
        border-radius: 2px;
        background: rgba(60, 60, 67, 0.14);
    }}
    QSlider::sub-page:horizontal {{
        height: 4px;
        border-radius: 2px;
        background: {ACCENT_BLUE};
    }}
    QSlider::handle:horizontal {{
        width: 14px; height: 14px;
        margin: -6px 0;
        border-radius: 7px;
        background: #FFFFFF;
        border: 1px solid rgba(0, 0, 0, 0.06);
    }}
    """
