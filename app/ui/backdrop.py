"""桌面背景取样：把窗口背后的屏幕截下来，重模糊 + 提取环境色。

这是"柔光玻璃"的地基。澎湃 OS 4 那套材质的要点是**感知融色**：
玻璃不是一块固定的白色半透明板，它透出背后的内容，并从背景里取一个色调回来，
所以换壁纸整个界面跟着变。Windows 上等价的做法就是自己截屏：

    capture_window_rect() → 缩小 → 反复平滑缩放（近似高斯）→ 提取主色

几个刻意的选择：
  * **重度缩小再放大** 当模糊用：比真卷积快两个数量级，而且效果更"柔"（没有振铃）；
  * **只统计有颜色的像素** 求环境色：否则一片灰底会把任何色调冲淡成灰；
  * **截到纯黑就判定失败**：全屏独显游戏 / 远程桌面下 grabWindow 会返回黑图，
    这种情况必须回落到静态玻璃，而不是把界面刷黑。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPixmap

log = logging.getLogger(__name__)

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


def capture_window_rect(rect: QRect, size_hint: Optional[object] = None) -> Optional[tuple[QPixmap, BackdropPalette]]:
    """截取屏幕上的 `rect` 区域（全局坐标），返回模糊底图与环境色。

    返回的 pixmap 尺寸等于 rect 的逻辑尺寸，绘制时可以 1:1 贴到窗口坐标上。
    """
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

    palette = analyse(image)
    blurred = blur_image(image)
    return QPixmap.fromImage(blurred), palette


def backdrop_from_rect(rect: QRectF) -> Optional[tuple[QPixmap, BackdropPalette]]:  # pragma: no cover - 便捷包装
    return capture_window_rect(QRect(int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height())))
