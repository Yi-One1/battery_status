"""设置项的有效性（问题7）。

「真实毛玻璃」这个开关按下去没有任何变化，原因是它只在"截不到桌面"那条
几乎不会走到的分支里生效；底图改成整屏取样之后，那条分支实际上形同虚设。
已经删掉这个开关、配套的 DWM Acrylic 调用和 `app/ui/winblur.py`。
这里锁住"删干净了"和"老配置文件不会炸"。
"""
from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QLabel

from app.core.config import DeviceRegistry, Preferences, SettingsStore
from app.ui.panel import SHELL_ALPHA_STATIC, SHELL_ALPHA_WITH_BACKDROP, shell_alpha
from app.ui.settings import SettingsPane


@pytest.fixture()
def env(qapp, tmp_path):
    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    return registry, store


# --------------------------------------------------------------------------- #
# 开关已经删掉
# --------------------------------------------------------------------------- #
def test_preferences_no_longer_has_glass_blur():
    assert not hasattr(Preferences(), "glass_blur")


def test_settings_page_has_no_glass_toggle(env):
    registry, store = env
    pane = SettingsPane(registry, store)

    assert not hasattr(pane, "blur_toggle")
    texts = [label.text() for label in pane.findChildren(QLabel)]
    assert not any("真实毛玻璃" in text for text in texts)


def test_winblur_module_is_gone():
    with pytest.raises(ImportError):
        import app.ui.winblur  # noqa: F401


# --------------------------------------------------------------------------- #
# 不透明度只剩一种判断依据：有没有取到桌面底图
# --------------------------------------------------------------------------- #
def test_shell_alpha_depends_only_on_having_a_backdrop():
    assert shell_alpha(True) == SHELL_ALPHA_WITH_BACKDROP
    assert shell_alpha(False) == SHELL_ALPHA_STATIC
    # 有底图时更透（让壁纸颜色上来），没底图时更实（保证可读）
    assert SHELL_ALPHA_WITH_BACKDROP < SHELL_ALPHA_STATIC


# --------------------------------------------------------------------------- #
# 老配置文件里有 glass_blur，也不能炸
# --------------------------------------------------------------------------- #
def test_old_preferences_with_glass_blur_still_loads(env, tmp_path):
    registry, _ = env
    path = tmp_path / "preferences.json"
    path.write_text(
        json.dumps({"glass_blur": True, "refresh_seconds": 30, "demo": False}),
        encoding="utf-8",
    )
    store = SettingsStore(registry, path=path)
    prefs = store.load()

    assert prefs.refresh_seconds == 30
    assert not hasattr(prefs, "glass_blur")
