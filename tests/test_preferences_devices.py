"""逐设备自定义外观测试（问题4）。

规则：单台设备可以覆盖颜色与样式，没覆盖的维度跟随所属类型；
非法值一律丢弃并回落到类型默认，绝不让界面拿到坏颜色。
"""
from __future__ import annotations

import pytest

from app.core.config import DeviceConfig, DeviceRegistry, Preferences, SettingsStore, TypeConfig

DEVICE = "logitech-gpx-superlight"
OTHER = "edifier-g3max"


@pytest.fixture()
def env(qapp, tmp_path):
    registry = DeviceRegistry(user_dir=tmp_path)
    store = SettingsStore(registry, path=tmp_path / "preferences.json")
    return registry, store


def cfg(registry: DeviceRegistry, device_id: str) -> DeviceConfig:
    found = registry.get(device_id)
    assert found is not None
    return found


# --------------------------------------------------------------------------- #
# 取值规则
# --------------------------------------------------------------------------- #
def test_device_follows_type_by_default(env):
    registry, store = env
    mouse = cfg(registry, DEVICE)
    prefs = store.prefs
    prefs.colors["mouse"] = "#123456"
    prefs.styles["mouse"] = "bar"

    assert prefs.color_for_device(mouse) == "#123456"
    assert prefs.style_for_device(mouse) == "bar"
    assert prefs.has_device_override(DEVICE) is False


def test_device_color_overrides_type(env):
    registry, store = env
    mouse = cfg(registry, DEVICE)
    prefs = store.prefs
    prefs.colors["mouse"] = "#123456"

    assert prefs.set_device_color(DEVICE, "#FF0000") is True
    assert prefs.color_for_device(mouse) == "#FF0000"
    # 同类型的其他设备不受影响
    assert prefs.color_for("mouse") == "#123456"
    assert prefs.has_device_override(DEVICE) is True


def test_device_style_overrides_type(env):
    registry, store = env
    mouse = cfg(registry, DEVICE)
    prefs = store.prefs
    prefs.styles["mouse"] = "ring"

    assert prefs.set_device_style(DEVICE, "number") is True
    assert prefs.style_for_device(mouse) == "number"
    assert prefs.style_for("mouse") == "ring"


def test_color_and_style_are_independent(env):
    registry, store = env
    mouse = cfg(registry, DEVICE)
    prefs = store.prefs
    prefs.styles["mouse"] = "bar"

    prefs.set_device_color(DEVICE, "#00FF88")
    # 只覆盖了颜色，样式仍跟随类型
    assert prefs.style_for_device(mouse) == "bar"
    assert prefs.color_for_device(mouse) == "#00FF88"


def test_two_devices_of_same_type_are_separate(env):
    registry, store = env
    prefs = store.prefs
    registry.add_device(DeviceConfig(id="mouse-b", type="mouse", display_name="第二只鼠标"))

    prefs.set_device_color(DEVICE, "#111111")
    prefs.set_device_color("mouse-b", "#222222")

    assert prefs.color_for_device(cfg(registry, DEVICE)) == "#111111"
    assert prefs.color_for_device(cfg(registry, "mouse-b")) == "#222222"


def test_accepts_id_or_tuple(env):
    registry, store = env
    prefs = store.prefs
    prefs.set_device_color(DEVICE, "#ABCDEF")

    assert prefs.color_for_device(DEVICE) == "#ABCDEF"
    assert prefs.color_for_device((DEVICE, "mouse")) == "#ABCDEF"


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #
def test_invalid_color_is_rejected(env):
    _, store = env
    prefs = store.prefs
    assert prefs.set_device_color(DEVICE, "红色") is False
    assert prefs.set_device_color(DEVICE, "") is False
    assert DEVICE not in prefs.device_colors


@pytest.mark.parametrize("style", ["", "donut", "RING", None])
def test_invalid_style_is_rejected(env, style):
    _, store = env
    assert store.prefs.set_device_style(DEVICE, style) is False
    assert DEVICE not in store.prefs.device_styles


def test_bad_values_are_dropped_on_load(env, tmp_path):
    registry, store = env
    bad = tmp_path / "preferences.json"
    bad.write_text(
        '{"device_colors": {"%s": "紫色", "ok": "#00AAFF"},'
        ' "device_styles": {"%s": "cube", "ok": "bar"}}' % (DEVICE, DEVICE),
        encoding="utf-8",
    )
    loaded = SettingsStore(registry, path=bad).prefs

    assert DEVICE not in loaded.device_colors, "坏颜色必须丢掉，让它回落到类型默认"
    assert loaded.device_colors["ok"] == "#00AAFF"
    assert DEVICE not in loaded.device_styles
    assert loaded.device_styles["ok"] == "bar"


# --------------------------------------------------------------------------- #
# 持久化与重置
# --------------------------------------------------------------------------- #
def test_overrides_persist(env, tmp_path):
    registry, store = env
    store.prefs.set_device_color(DEVICE, "#0F0F0F")
    store.prefs.set_device_style(DEVICE, "bar")
    store.save()

    again = SettingsStore(registry, path=tmp_path / "preferences.json").prefs
    assert again.device_colors[DEVICE] == "#0F0F0F"
    assert again.device_styles[DEVICE] == "bar"


def test_reset_returns_to_type_default(env):
    registry, store = env
    prefs = store.prefs
    prefs.colors["mouse"] = "#123456"
    prefs.styles["mouse"] = "number"
    prefs.set_device_color(DEVICE, "#FF0000")
    prefs.set_device_style(DEVICE, "ring")

    prefs.reset_device(DEVICE)

    mouse = cfg(registry, DEVICE)
    assert prefs.has_device_override(DEVICE) is False
    assert prefs.color_for_device(mouse) == "#123456"
    assert prefs.style_for_device(mouse) == "number"


def test_leftover_override_for_removed_device_is_harmless(env):
    """设备删了以后留着它的颜色值不该出事，也不该影响别人。"""
    registry, store = env
    store.prefs.set_device_color(OTHER, "#00FF00")
    registry.remove_device(OTHER, persist=False)

    assert store.prefs.color_for_device(cfg(registry, DEVICE)) == store.prefs.color_for("mouse")
    assert store.prefs.device_colors[OTHER] == "#00FF00"


# --------------------------------------------------------------------------- #
# 界面联动
# --------------------------------------------------------------------------- #
def test_settings_exposes_controls_per_device(env):
    from app.ui.settings import SettingsPane

    registry, store = env
    pane = SettingsPane(registry, store)

    assert set(pane._device_swatches) == {d.id for d in registry.enabled_devices()}
    assert set(pane._device_segments) == {d.id for d in registry.enabled_devices()}


def test_settings_style_change_persists_and_signals(env, tmp_path):
    from app.ui.settings import SettingsPane

    registry, store = env
    pane = SettingsPane(registry, store)
    fired = []
    pane.changed.connect(lambda: fired.append(True))

    pane._on_device_style(DEVICE, "bar")

    assert store.prefs.style_for_device(cfg(registry, DEVICE)) == "bar"
    assert store.prefs.style_for("mouse") != "bar", "改单台设备不该动到类型默认"
    assert fired == [True]
    assert "已自定义" in pane._device_hints[DEVICE].text()
    assert SettingsStore(registry, path=tmp_path / "preferences.json").prefs.device_styles[DEVICE] == "bar"


def test_settings_reset_device_appearance(env):
    from app.ui.settings import SettingsPane

    registry, store = env
    pane = SettingsPane(registry, store)
    pane._on_device_style(DEVICE, "number")

    assert pane.reset_device_appearance(DEVICE) is True
    assert store.prefs.has_device_override(DEVICE) is False
    assert "已自定义" not in pane._device_hints[DEVICE].text()
    # 再点一次已无自定义可撤
    assert pane.reset_device_appearance(DEVICE) is False


def test_panel_card_uses_per_device_appearance(env):
    from app.core.monitor import Monitor
    from app.ui.panel import Panel
    from app.ui.tray import TrayController

    registry, store = env
    panel = Panel(registry, store, Monitor(registry, store.prefs), TrayController(registry, store))
    try:
        store.prefs.set_device_color(DEVICE, "#FF00AA")
        store.prefs.set_device_style(DEVICE, "bar")
        store.prefs.colors["mouse"] = "#0000FF"

        # 设置页改动 → changed 信号 → 面板即时重绘
        panel.settings.changed.emit()

        card = panel._cards[DEVICE]
        assert card.color == "#FF00AA"
        assert card.style == "bar"
        # 其它设备仍走类型默认
        assert panel._cards[OTHER].color != "#FF00AA"
    finally:
        panel.deleteLater()


def test_panel_rebuild_keeps_device_overrides(env):
    from app.core.monitor import Monitor
    from app.ui.panel import Panel
    from app.ui.tray import TrayController

    registry, store = env
    panel = Panel(registry, store, Monitor(registry, store.prefs), TrayController(registry, store))
    try:
        store.prefs.set_device_color(DEVICE, "#123123")
        store.prefs.set_device_style(DEVICE, "number")
        panel._rebuild_cards()          # 例如新增/删除设备后重建
        card = panel._cards[DEVICE]
        assert (card.color, card.style) == ("#123123", "number")
    finally:
        panel.deleteLater()


def test_type_default_still_applies_to_type_level_helper(env):
    """按类型的老接口不能被破坏，类型行控件还在用。"""
    _, store = env
    store.prefs.colors["headset"] = "#0A0A0A"
    assert store.prefs.color_for("headset") == "#0A0A0A"
    assert isinstance(store.prefs.styles, dict)
    assert isinstance(store.prefs.device_styles, dict)


def test_type_config_unaffected():
    t = TypeConfig("mouse", "鼠标", "#0A84FF", "ring")
    assert (t.key, t.label, t.color, t.style) == ("mouse", "鼠标", "#0A84FF", "ring")


def test_preferences_dict_roundtrip_keeps_device_maps(env):
    _, store = env
    prefs = store.prefs
    prefs.set_device_color(DEVICE, "#AAAAAA")
    prefs.set_device_style(DEVICE, "bar")

    restored = Preferences.from_dict(prefs.to_dict(), store.registry)
    assert restored.device_colors[DEVICE] == "#AAAAAA"
    assert restored.device_styles[DEVICE] == "bar"
