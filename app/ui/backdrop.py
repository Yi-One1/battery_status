"""桌面背景取样：把屏幕截下来，重模糊 + 提取环境色。

这是"柔光玻璃"的地基。澎湃 OS 4 那套材质的要点是**感知融色**：
玻璃不是一块固定的白色半透明板，它透出背后的内容，并从背景里取一个色调回来，
所以换壁纸整个界面跟着变。

**为什么取整屏而不是取窗口那一块**：
只取窗口大小的话，面板一被拖动，玻璃里透的仍是打开那一刻的桌面 —— 位置全错了。
取整屏、把板材原点和缩放一起记住，绘制时按"控件的屏幕坐标"去取对应像素，
拖动时颜色自然跟着走，换壁纸只需过几秒重新取一次。

几个刻意的选择：
  * **先缩到 1/8 再模糊**：既当模糊用，又把板材从 14MB 压到 0.9MB（低占用）；
  * **只统计有颜色的像素**求环境色，否则一片灰底会把任何色调冲淡成灰；
  * **重新取样前先把面板自己占的那块抹平**（`_smear_rect`）：
    否则每轮取样都会把面板自己的亮色糊进底图，玻璃越刷新越白；
  * **截到纯黑就判失败**：全屏独显游戏 / 远程桌面下 grabWindow 会返回黑图，
    这种情况必须回落到静态玻璃，而不是把界面刷黑。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPixmap

log = logging.getLogger(__name__)

# 板材相对屏幕的缩小比例：1 像素板材 = SCALE 个逻辑像素
SCALE = 8
# 模糊强度：先缩到这个宽度的 1/BLUR_DIVISOR，再平滑放大回去
BLUR_DIVISOR = 8
BLUR_PASSES = 2
# 求环境色时的采样尺寸
SAMPLE_SIZE = 32
# 低于这个饱和度的像素不参与"主色"统计，避免灰墙把色相拉没
CHROMA_THRESHOLD = 0.06
# 环境色饱和度超过这个值就认为背景"有明确色调"
VIVID_SATURATION = 0.16


@dataclass(frozen=True)
class BackdropPalette:
    """从背景里读到的"环境信息"。"""

    color: QColor          # 代表性的环境色（已降饱和，可直接当玻璃底色）
    luminance: float       # 平均亮度 0~1
    saturation: float      # 环境色饱和度 0~1
    vivid: bool            # 背景是否有明显色调

    @property
    def is_dark(self) -> bool:
        return self.luminance < 0.42


DEFAULT_PALETTE = BackdropPalette(QColor("#EDF1F8"), 0.92, 0.04, False)


def _luma(r: int, g: int, b: int) -> float:
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def analyse(image: QImage) -> BackdropPalette:
    """统计亮度与主色。输入越小越快，调用方先缩小。"""
    if image.isNull() or image.width() == 0 or image.height() == 0:
        return DEFAULT_PALETTE
    sample = image.convertToFormat(QImage.Format.Format_RGB32).scaled(
        SAMPLE_SIZE,
        SAMPLE_SIZE,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )

    total_luma = 0.0
    pixels = 0
    chroma_r = chroma_g = chroma_b = 0.0
    chroma_weight = 0.0

    for y in range(sample.height()):
        for x in range(sample.width()):
            color = sample.pixelColor(x, y)
            r, g, b = color.red(), color.green(), color.blue()
            lum = _luma(r, g, b)
            total_luma += lum
            pixels += 1

            saturation = color.saturationF()
            if saturation >= CHROMA_THRESHOLD:
                # 亮度加权：暗部颜色不可靠，权重低一些
                weight = saturation * (0.35 + 0.65 * lum)
                chroma_r += r * weight
                chroma_g += g * weight
                chroma_b += b * weight
                chroma_weight += weight

    if not pixels:
        return DEFAULT_PALETTE

    luminance = total_luma / pixels
    if chroma_weight <= 1e-6:
        # 纯灰背景：没有色调可借，给一个中性的冷白
        return BackdropPalette(QColor("#E9EDF5"), luminance, 0.0, False)

    color = QColor(
        int(chroma_r / chroma_weight),
        int(chroma_g / chroma_weight),
        int(chroma_b / chroma_weight),
    )
    # 降饱和：环境色是用来"融"的，不是用来染色的
    hsl_h, hsl_s, hsl_l = color.hueF(), color.saturationF(), color.lightnessF()
    tinted = QColor.fromHslF(
        max(0.0, hsl_h),
        min(0.42, hsl_s * 0.55),
        min(0.86, max(0.30, hsl_l * 0.55 + 0.42)),
    )
    # 是否有明确色调：只看色度，不看明暗反差。
    # 早先用 (luma_max - luma_min) > 0.12 判断，会把"一整块暗蓝壁纸"判成没有色调，
    # 而那恰恰是最该让界面变色的一类壁纸。
    vivid = hsl_s > VIVID_SATURATION
    return BackdropPalette(tinted, luminance, hsl_s, vivid)


def blur_image(image: QImage, divisor: int = BLUR_DIVISOR, passes: int = BLUR_PASSES) -> QImage:
    """缩小-放大近似高斯模糊；结果尺寸与原图一致。"""
    if image.isNull():
        return image
    width = max(1, image.width() // max(1, divisor))
    height = max(1, image.height() // max(1, divisor))
    small = image.scaled(
        width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    # 缩小时抗锯齿已在做平均；再来一轮去掉块状感
    for _ in range(max(0, passes - 1)):
        small = small.scaled(
            max(1, width // 2),
            max(1, height // 2),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ).scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return small.scaled(
        image.width(), image.height(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
    )


def _is_blank_capture(image: QImage) -> bool:
    """grabWindow 在独显全屏 / 无桌面合成时会返回整块黑或整块透明。"""
    if image.isNull() or image.width() == 0:
        return True
    probe = image.scaled(8, 8, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
    colors = {probe.pixelColor(x, y).rgb() & 0xFFFFFF for y in range(8) for x in range(8)}
    if len(colors) > 1:
        return False
    only = QColor(colors.pop())
    return _luma(only.red(), only.green(), only.blue()) < 0.03


def smear_rect(image: QImage, rect: QRect) -> None:
    """把 rect 里的像素沿水平方向用两侧边缘做线性插值填平。

    用途：面板显示期间重新取样时，屏幕上那一块是**面板自己**。
    不抹掉的话，每刷新一次底图就把面板的亮色糊进去一层，玻璃会越来越白。
    底图是重模糊的，横向插值在视觉上和周围完全接得上。

    边界处理：某一侧贴着屏幕边缘时，就用另一侧的颜色铺满（总比留一块假的亮斑好）。
    """
    if image.isNull() or rect.isEmpty():
        return
    left = max(0, rect.left())
    right = min(image.width() - 1, rect.right())
    top = max(0, rect.top())
    bottom = min(image.height() - 1, rect.bottom())
    if right <= left or bottom <= top:
        return
    if image.format() != QImage.Format.Format_RGB32:
        image.convertTo(QImage.Format.Format_RGB32)

    for y in range(top, bottom + 1):
        if left > 0:
            start = image.pixelColor(left - 1, y)
        elif right + 1 < image.width():
            start = image.pixelColor(right + 1, y)
        else:
            continue
        stop = image.pixelColor(right + 1, y) if right + 1 < image.width() else start
        span = right - left + 2
        for x in range(left, right + 1):
            t = (x - left + 1) / span
            image.setPixelColor(
                x,
                y,
                QColor(
                    round(start.red() + (stop.red() - start.red()) * t),
                    round(start.green() + (stop.green() - start.green()) * t),
                    round(start.blue() + (stop.blue() - start.blue()) * t),
                ),
            )


@dataclass(frozen=True)
class Backdrop:
    """一块可以直接用来画的桌面底图，自带坐标系。"""

    pixmap: QPixmap
    palette: BackdropPalette
    origin: QPoint      # pixmap 左上角对应的屏幕全局坐标
    scale: float        # pixmap 上 1 像素 = scale 个逻辑像素

    def is_valid(self) -> bool:
        return not self.pixmap.isNull() and self.scale > 0


def _grab(rect: QRect) -> Optional[QImage]:
    """按全局坐标截一块屏幕；空白 / 失败返回 None。"""
    if rect.width() <= 0 or rect.height() <= 0:
        return None
    screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
    if screen is None:
        return None
    try:
        shot = screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
    except Exception as exc:  # noqa: BLE001
        log.debug("截屏失败：%s", exc)
        return None
    if shot.isNull():
        return None
    image = shot.toImage()
    # 统一成"1 像素 = 1 逻辑像素"，上层绘制时才不用管 DPI 换算
    image.setDevicePixelRatio(1.0)
    if _is_blank_capture(image):
        log.debug("截屏为空白，改用静态玻璃")
        return None
    return image


def capture_screen(exclude: Optional[QRect] = None) -> Optional[Backdrop]:
    """截当前屏幕整屏，缩到 1/SCALE 并模糊，返回带坐标系的底图。

    exclude 是**现在被本程序自己占住**的区域（面板窗口矩形，全局坐标），
    取样时会被抹平，避免把面板画进底图。
    """
    screen = QGuiApplication.primaryScreen()
    target = screen
    if exclude is not None:
        target = QGuiApplication.screenAt(exclude.center()) or screen
    if target is None:
        return None
    geometry = target.geometry()
    image = _grab(geometry)
    if image is None:
        return None

    width = max(2, image.width() // SCALE)
    height = max(2, image.height() // SCALE)
    small = image.scaled(
        width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
    )

    if exclude is not None:
        local = QRect(
            (exclude.x() - geometry.x()) // SCALE,
            (exclude.y() - geometry.y()) // SCALE,
            max(1, exclude.width() // SCALE),
            max(1, exclude.height() // SCALE),
        )
        smear_rect(small, local)

    palette = analyse(small)
    # 已经在 1/SCALE 尺度上了，再来一轮半分辨率往返就够了
    blurred = blur_image(small, divisor=2, passes=2)
    return Backdrop(
        pixmap=QPixmap.fromImage(blurred),
        palette=palette,
        origin=geometry.topLeft(),
        scale=float(SCALE),
    )
