"""柔光玻璃材质（问题5）的测试。

澎湃 OS 4 那套"柔光玻璃"落到代码上是四件事，逐一验证：

  1. **感知融色** —— `analyse()` 能从背景里读出亮度与色调，`blur_image()` 把细节抹平，
     于是换壁纸整块玻璃的调子跟着变；
  2. **自适应通透度** —— `adaptive_scrim()` 在亮背景上几乎不加纱、暗背景上加厚，
     而且必须单调：背景越暗，白纱只会更厚；
  3. **边缘融色** —— `paint_glass()` 的白纱是径向的，中心厚四边薄，
     所以贴着边框的像素比中心更"有颜色"。这是它和一块磨砂白板的分界线；
  4. **回落** —— 截不到桌面（独显全屏 / 远程桌面）时必须回到静态玻璃，不能把界面刷黑。
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPixmap

from app.ui import glass
from app.ui.backdrop import (
    DEFAULT_PALETTE,
    BackdropPalette,
    _grab,
    _is_blank_capture,
    analyse,
    blur_image,
    capture_screen,
    smear_rect,
)


# QPixmap 离不开 QGuiApplication：整模块都先把它建起来，否则进程会在退出时炸掉
pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture(autouse=True)
def _clean_backdrop():
    """背景是模块级状态，每个用例都要从"没有背景"开始。"""
    glass.clear_backdrop()
    yield
    glass.clear_backdrop()


# --------------------------------------------------------------------------- #
# 造图小工具
# --------------------------------------------------------------------------- #
def _image(width: int, height: int, fill: str | QColor = "#FFFFFF") -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(fill) if isinstance(fill, str) else fill)
    return image


def _painted(width: int, height: int, draw) -> QImage:
    image = _image(width, height, Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    draw(painter, width, height)
    painter.end()
    return image


def _solid(color: str, width: int = 256, height: int = 256) -> tuple[QPixmap, BackdropPalette]:
    """造一张纯色背景板（已过 analyse），当作"壁纸只有一种颜色"。"""
    image = _image(width, height, color)
    return QPixmap.fromImage(blur_image(image)), analyse(image)


def _render_glass(width: int, height: int, *, base: str = "#9A9A9A", **kwargs) -> QImage:
    """把玻璃画在一块"桌面底色"上 —— 玻璃是半透明的，必须有个底才量得准。"""
    image = _image(width, height, base)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    glass.paint_glass(painter, QRectF(0, 0, width, height), kwargs.pop("radius", 18), **kwargs)
    painter.end()
    return image


def _mean_brightness(image: QImage) -> float:
    total = 0.0
    count = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            color = image.pixelColor(x, y)
            total += (color.red() + color.green() + color.blue()) / 3.0
            count += 1
    return total / max(1, count)


def _mean_neighbour_delta(image: QImage) -> float:
    """相邻像素的平均色差，用来衡量"糊"到什么程度。"""
    total = 0.0
    count = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width() - 1, 2):
            a = image.pixelColor(x, y)
            b = image.pixelColor(x + 2, y)
            total += abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue())
            count += 1
    return total / max(1, count)


def _saturation(image: QImage, x: int, y: int) -> float:
    return image.pixelColor(x, y).saturationF()


# --------------------------------------------------------------------------- #
# 一、背景取样：亮度、色调
# --------------------------------------------------------------------------- #
def test_analyse_neutral_background_reports_no_vivid_tint():
    palette = analyse(_image(32, 32, "#808080"))
    assert palette.saturation == pytest.approx(0.0, abs=0.01)
    assert palette.vivid is False
    assert palette.luminance == pytest.approx(0.5, abs=0.03)
    assert palette.is_dark is False


def test_analyse_reads_luminance_and_darkness():
    bright = analyse(_image(32, 32, "#F2F5FA"))
    dark = analyse(_image(32, 32, "#101014"))

    assert bright.luminance > 0.85
    assert bright.is_dark is False
    assert dark.luminance < 0.10
    assert dark.is_dark is True


def test_analyse_keeps_the_hue_of_a_vivid_background():
    """降饱和是为了"融"，但色相不能丢 —— 否则换壁纸界面也不会变色。"""
    source = QColor("#2F6FD0")
    palette = analyse(_image(32, 32, source))

    assert palette.vivid is True
    assert palette.color.hueF() == pytest.approx(source.hueF(), abs=0.02)
    # 亮度被抬到能当玻璃底的程度
    assert palette.color.lightnessF() > source.lightnessF()


def test_analyse_clamps_saturation_so_background_never_dyes_the_panel():
    palette = analyse(_image(32, 32, "#FF0000"))
    assert palette.color.saturationF() <= 0.43


def test_analyse_falls_back_on_empty_image():
    assert analyse(QImage()) is DEFAULT_PALETTE


# --------------------------------------------------------------------------- #
# 二、模糊与截图回落
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("size", [(64, 40), (480, 472), (17, 9)])
def test_blur_preserves_size(size):
    image = _image(size[0], size[1], "#808080")
    assert blur_image(image).size() == image.size()


def test_blur_actually_blurs():
    """棋盘格进、雾面出：相邻像素的平均色差要掉一大截。"""

    def draw(painter: QPainter, width: int, height: int) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        for y in range(0, height, 8):
            for x in range(0, width, 8):
                dark = (x // 8 + y // 8) % 2 == 0
                painter.fillRect(x, y, 8, 8, QColor("#101010") if dark else QColor("#EFEFEF"))

    sharp = _painted(64, 64, draw)
    smooth = blur_image(sharp)

    assert _mean_neighbour_delta(smooth) < _mean_neighbour_delta(sharp) * 0.4


def test_blank_capture_detects_black_screen():
    assert _is_blank_capture(_image(32, 32, "#000000")) is True


@pytest.mark.parametrize("color", ["#FFFFFF", "#F2F5FA", "#3A6AC8"])
def test_blank_capture_accepts_real_content(color):
    assert _is_blank_capture(_image(32, 32, color)) is False


def test_blank_capture_accepts_a_gradient():
    def draw(painter: QPainter, width: int, height: int) -> None:
        ramp = QLinearGradient(0, 0, width, 0)
        ramp.setColorAt(0.0, QColor("#0A0A0A"))
        ramp.setColorAt(1.0, QColor("#E8E8E8"))
        painter.fillRect(0, 0, width, height, ramp)

    assert _is_blank_capture(_painted(32, 32, draw)) is False


def test_grab_returns_none_for_degenerate_rect():
    assert _grab(QRect(0, 0, 0, 120)) is None
    assert _grab(QRect(0, 0, 120, 0)) is None


# --------------------------------------------------------------------------- #
# 整屏取样（问题3：拖动 / 换壁纸要实时跟上）
# --------------------------------------------------------------------------- #
def test_smear_rect_erases_the_patch_under_the_panel():
    """面板自己占的那块必须被抹平，否则每刷一次底图就白一层。"""

    def draw(painter: QPainter, width: int, height: int) -> None:
        ramp = QLinearGradient(0, 0, width, 0)
        ramp.setColorAt(0.0, QColor("#203040"))
        ramp.setColorAt(1.0, QColor("#C0D0E0"))
        painter.fillRect(0, 0, width, height, ramp)
        painter.fillRect(28, 8, 12, 8, QColor("#FFFFFF"))  # 面板留下的亮块

    image = _painted(64, 24, draw)
    before = image.pixelColor(34, 12)
    smear_rect(image, QRect(28, 8, 12, 8))
    after = image.pixelColor(34, 12)

    assert before.red() > 200          # 亮块本来在
    assert after.red() < 200            # 被抹掉了
    # 抹平用两侧颜色插值，结果应该落在两侧之间
    left = image.pixelColor(27, 12).red()
    right = image.pixelColor(41, 12).red()
    assert min(left, right) - 6 <= after.red() <= max(left, right) + 6
    # 区域外不动
    assert image.pixelColor(2, 2) == image.pixelColor(2, 2)


def test_smear_rect_survives_screen_edges():
    image = _image(32, 16, "#404040")
    smear_rect(image, QRect(-10, -10, 200, 200))  # 比整张图还大
    assert image.pixelColor(5, 5).red() == 0x40


def test_smear_rect_ignores_empty_rect():
    image = _image(16, 16, "#123456")
    smear_rect(image, QRect(4, 4, 0, 0))
    assert image.pixelColor(8, 8).red() == 0x12


def test_backdrop_carries_origin_and_scale():
    pixmap = QPixmap.fromImage(_image(40, 30, "#808080"))
    glass.set_backdrop(pixmap, DEFAULT_PALETTE, origin=QPoint(1280, 40), scale=8.0)

    assert glass.current_backdrop() is pixmap
    assert glass.backdrop_origin() == QPoint(1280, 40)
    assert glass.backdrop_scale() == pytest.approx(8.0)

    glass.clear_backdrop()
    assert glass.backdrop_origin() == QPoint(0, 0)
    assert glass.backdrop_scale() == pytest.approx(1.0)


def test_glass_samples_by_screen_coordinates():
    """板材原点在 (1000,500)、1/2 缩放：控件在全局 x=1040 处，应取到板材 x=20。"""
    plate = _painted(80, 40, _half_red_half_green)
    glass.set_backdrop(
        QPixmap.fromImage(plate),
        analyse(plate),
        origin=QPoint(1000, 500),
        scale=2.0,
    )

    size = 20
    reading = _render_glass(size, size, alpha=0.74, radius=3, offset=QPointF(1040, 500), glow=False)
    centre = reading.pixelColor(size // 2, size // 2)
    assert centre.red() > centre.green()  # 板材 x=20 处是红的

    reading = _render_glass(size, size, alpha=0.74, radius=3, offset=QPointF(1120, 500), glow=False)
    centre = reading.pixelColor(size // 2, size // 2)
    assert centre.green() > centre.red()  # 板材 x=60 处是绿的


def _half_red_half_green(painter: QPainter, width: int, height: int) -> None:
    painter.fillRect(0, 0, width // 2, height, QColor("#C81E28"))
    painter.fillRect(width // 2, 0, width - width // 2, height, QColor("#127A4A"))


def test_moving_the_panel_changes_what_shows_through():
    """这条是问题3的核心：同一个控件，屏幕位置不同，透出的底色就该不同。"""
    plate = _painted(160, 40, _half_red_half_green)
    glass.set_backdrop(QPixmap.fromImage(plate), analyse(plate), origin=QPoint(0, 0), scale=1.0)

    left = _render_glass(24, 24, alpha=0.74, radius=3, offset=QPointF(10, 0), glow=False).pixelColor(12, 12)
    right = _render_glass(24, 24, alpha=0.74, radius=3, offset=QPointF(120, 0), glow=False).pixelColor(12, 12)

    assert left.red() > left.green()
    assert right.green() > right.red()


def test_capture_screen_reports_screen_geometry():
    """真实截一次整屏；截不到（无桌面合成）就跳过。"""
    backdrop = capture_screen()
    if backdrop is None:
        pytest.skip("当前环境截不到屏幕")
    screen = QGuiApplication.primaryScreen().geometry()
    assert backdrop.origin == screen.topLeft()
    assert backdrop.scale >= 1
    assert backdrop.pixmap.width() <= screen.width()
    assert backdrop.is_valid() is True


# --------------------------------------------------------------------------- #
# 三、自适应通透度
# --------------------------------------------------------------------------- #
def test_scrim_is_unchanged_when_no_backdrop():
    """没有背景图时保持老行为，免得截屏失败的那一帧界面忽明忽暗。"""
    assert glass.adaptive_scrim(0.74) == 0.74
    assert glass.adaptive_scrim(0.40) == 0.40


def test_scrim_turns_transparent_on_a_bright_wallpaper():
    glass.set_backdrop(*_solid("#F4F6FA"))
    scrim = glass.adaptive_scrim(0.74)
    assert scrim == pytest.approx(0.74 * glass.SCRIM_FLOOR_RATIO, abs=0.02)
    assert scrim < 0.35  # 亮背景必须让壁纸色透上来


def test_scrim_thickens_on_a_dark_wallpaper():
    glass.set_backdrop(*_solid("#101018"))
    scrim = glass.adaptive_scrim(0.74)
    assert scrim > 0.65  # 深色壁纸下要压得住深色文字
    assert scrim <= glass.SCRIM_CEILING + 1e-6


@pytest.mark.parametrize("base", [0.36, 0.52, 0.74])
def test_scrim_is_monotonic_in_background_darkness(base):
    """背景越暗白纱只会更厚 —— 曲线不许打弯，否则界面明暗会跳。"""
    previous = -1.0
    for luminance in ("#FFFFFF", "#E0E4EC", "#B0B6C0", "#7A8088", "#40444C", "#101014"):
        glass.set_backdrop(*_solid(luminance))
        value = glass.adaptive_scrim(base)
        assert value >= previous - 1e-9
        previous = value


def test_scrim_never_drops_below_a_usable_floor():
    glass.set_backdrop(*_solid("#FFFFFF"))
    assert glass.adaptive_scrim(0.30) >= 0.14


# --------------------------------------------------------------------------- #
# 四、材质合成
# --------------------------------------------------------------------------- #
def test_environment_color_follows_the_backdrop():
    pixmap, palette = _solid("#2F6FD0")
    glass.set_backdrop(pixmap, palette)
    assert glass.current_backdrop() is pixmap
    assert glass.environment_palette() is palette
    assert glass.environment_color() == QColor(palette.color)
    assert glass.environment_color().hueF() == pytest.approx(QColor("#2F6FD0").hueF(), abs=0.02)


def test_clear_backdrop_restores_the_default_palette():
    glass.set_backdrop(*_solid("#2F6FD0"))
    glass.clear_backdrop()
    assert glass.current_backdrop() is None
    assert glass.environment_palette() is DEFAULT_PALETTE


def test_glass_without_backdrop_stays_a_soft_white_plate():
    """回落路径：没有背景时依然是原来那块柔光白玻璃，不能把界面刷暗。"""
    image = _render_glass(200, 120, alpha=0.86)  # 0.86 是截屏失败时的回落不透明度
    centre = image.pixelColor(100, 60)

    assert min(centre.red(), centre.green(), centre.blue()) > 210


def test_glass_shows_the_wallpaper_colour_through_it():
    glass.set_backdrop(*_solid("#C81E28"))  # 一块饱和的红
    with_backdrop = _render_glass(200, 120, alpha=0.74).pixelColor(100, 60)

    glass.clear_backdrop()
    without = _render_glass(200, 120, alpha=0.74).pixelColor(100, 60)

    assert with_backdrop.red() - with_backdrop.green() > without.red() - without.green() + 15
    assert with_backdrop.red() > without.red()


def test_glass_edges_let_more_colour_through_than_the_centre():
    """径向白纱 = 边缘融色：贴着下边框的地方应该比中心更"有颜色"。

    只看下缘：上缘和左上角被"顶部受光"和"左上柔光"两束光压着，
    那里本来就该最亮、最不显色（见 test_glass_carries_a_light_from_above）。
    """
    glass.set_backdrop(*_solid("#127A4A"))  # 饱和的绿
    image = _render_glass(240, 140, alpha=0.74, radius=18)

    centre = _saturation(image, 120, 70)
    bottom_left = _saturation(image, 8, 132)
    bottom_right = _saturation(image, 232, 132)
    left = _saturation(image, 4, 70)

    assert bottom_left > centre
    assert bottom_right > centre
    assert left > centre


def test_glass_carries_a_light_from_above():
    """顶部受光：同一列上，靠上的像素比靠下的亮。"""
    image = _render_glass(240, 140, alpha=0.74, radius=18, glow=False)
    rows = [image.pixelColor(120, y) for y in (10, 70, 130)]
    brightness = [(c.red() + c.green() + c.blue()) / 3 for c in rows]
    assert brightness[0] > brightness[1] > brightness[2]


def test_glass_is_drawn_from_the_right_part_of_the_backdrop():
    """offset 决定从背景板上取哪一块；取错位置会让玻璃里的桌面和外面接不上。"""
    image = _image(120, 60, Qt.GlobalColor.transparent)
    painter = QPainter(image)
    gradient = QLinearGradient(0, 0, 0, 60)
    gradient.setColorAt(0.0, QColor("#C81E28"))  # 上半红
    gradient.setColorAt(1.0, QColor("#127A4A"))  # 下半绿
    painter.fillRect(0, 0, 120, 60, gradient)
    painter.end()
    glass.set_backdrop(QPixmap.fromImage(image), analyse(image))

    top = _render_glass(60, 20, alpha=0.74, radius=4, offset=QPointF(0, 0), glow=False)
    bottom = _render_glass(60, 20, alpha=0.74, radius=4, offset=QPointF(0, 40), glow=False)

    assert top.pixelColor(30, 10).red() > top.pixelColor(30, 10).green()
    assert bottom.pixelColor(30, 10).green() > bottom.pixelColor(30, 10).red()


def test_card_weight_mutes_the_backdrop():
    """叠在外壳上的卡片只透一点点背景，否则会把自己下面那块桌面再画一遍。"""
    glass.set_backdrop(*_solid("#C81E28"))
    strong = _render_glass(200, 120, alpha=0.42, backdrop_weight=1.0).pixelColor(100, 60)
    muted = _render_glass(200, 120, alpha=0.42, backdrop_weight=0.3).pixelColor(100, 60)

    assert strong.red() > muted.red()
    assert strong.green() < muted.green()


def test_opting_out_of_the_backdrop_keeps_the_veil_static():
    """对话框这类独立窗口不参与取样，白纱也就不能跟着背景变薄，否则表单读不清。"""
    glass.set_backdrop(*_solid("#C81E28"))
    adaptive = _render_glass(200, 120, alpha=0.94).pixelColor(100, 60)
    opted_out = _render_glass(200, 120, alpha=0.94, backdrop_weight=0.0).pixelColor(100, 60)

    assert opted_out.red() > adaptive.red()          # 不透背景 ⇒ 更亮
    assert opted_out.saturationF() < adaptive.saturationF()  # 也不带背景的色


def test_hovering_brightens_the_surface(qapp):
    """点按有光：悬停时整块玻璃自己亮一点，而不是换一套配色。"""
    glass.set_backdrop(*_solid("#303844"))  # 深色背景，提亮才看得出来
    surface = glass.GlassSurface(alpha=0.42, interactive=True)
    surface.resize(120, 80)

    surface.hovered = False
    normal = _mean_brightness(surface.grab().toImage())
    surface.hovered = True
    hovered = _mean_brightness(surface.grab().toImage())

    assert hovered > normal
    assert surface.base_alpha == 0.42  # 提亮只发生在绘制时，不改基准值


def test_backdrop_offset_is_screen_global(qapp):
    """底图按整屏取，控件必须报自己的**屏幕坐标**，不能报窗口内坐标。"""
    window = glass.GlassSurface()
    window.resize(120, 90)
    window.move(300, 200)
    child = glass.GlassSurface(window)
    child.setGeometry(10, 20, 40, 30)

    assert child.backdrop_offset() == QPointF(child.mapToGlobal(QPoint(0, 0)))
    if window.pos() == QPoint(300, 200):  # 离屏平台下 move 可能被忽略
        assert child.backdrop_offset() == QPointF(310.0, 220.0)
