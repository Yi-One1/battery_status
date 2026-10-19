"""设备发现测试（问题3 的自动新增层）。

用真实的 HID 枚举形态造数据：一台 2.4G 接收器会以同一个 VID:PID 冒出
键盘页、鼠标页、Consumer 页、厂商页等多个接口，发现层必须把它们归并成一条。
"""
from __future__ import annotations

import pytest

from app.core.config import SOURCE_AUTO, SOURCE_USER, DeviceConfig, DeviceRegistry
from app.core.discovery import (
    DiscoveredDevice,
    available_devices,
    build_device_config,
    build_manual_config,
    describe_group,
    group_covered,
    group_infos,
    is_generic_name,
    make_device_id,
    suggest_type,
)
from app.core.hidapi_io import HidDeviceInfo


def hid(vid: int, pid: int, page: int, product: str = "", path: str = "", usage: int = 0x06, iface: int = 1):
    return HidDeviceInfo(
        path=(path or f"{vid:04x}:{pid:04x}:{page:04x}:{iface}").encode(),
        vid=vid,
        pid=pid,
        product=product,
        manufacturer="",
        usage_page=page,
        usage=usage,
        interface=iface,
    )


RECEIVER = [
    hid(0x258A, 0x01BB, 0x0001, "2.4G Wireless Receiver", path="if0-kbd", iface=0),
    hid(0x258A, 0x01BB, 0x0001, "2.4G Wireless Receiver", path="if1-mouse", iface=1, usage=0x02),
    hid(0x258A, 0x01BB, 0x000C, "2.4G Wireless Receiver", path="if1-consumer", iface=1),
    hid(0x258A, 0x01BB, 0xFF00, "2.4G Wireless Receiver", path="if1-vendor00", iface=1),
    hid(0x258A, 0x01BB, 0xFF02, "2.4G Wireless Receiver", path="if1-vendor02", iface=1),
    # hidapi 常见：同一 path 被按顶层集合重复列出
    hid(0x258A, 0x01BB, 0xFF02, "2.4G Wireless Receiver", path="if1-vendor02", iface=1),
]


@pytest.fixture()
def registry(tmp_path):
    """只保留用户层：内置设备清空，断言不会被真实机器上的配置影响。"""
    reg = DeviceRegistry(user_dir=tmp_path)
    reg._bundled_devices = []
    reg._rebuild()
    return reg


def test_group_infos_merges_interfaces_by_vid_pid():
    groups = group_infos(RECEIVER)
    assert list(groups) == [(0x258A, 0x01BB)]


def test_describe_group_counts_unique_paths():
    device = describe_group(RECEIVER)
    assert device.key == "258A:01BB"
    assert device.name == "2.4G Wireless Receiver"
    assert device.vendor_specific is True
    assert 0xFF00 in device.usage_pages
    # 6 条记录里有一条 path 重复，去重后是 5 个接口
    assert device.interfaces == 5


def test_suggest_type_by_keyword():
    assert suggest_type("2.4G Wireless Receiver", "") == "mouse"
    assert suggest_type("G98 PRO Keyboard", "") == "keyboard"
    assert suggest_type("", "HECATE") in ("mouse", "headset")
    assert suggest_type("HECATE G3 MAX 无线耳机", "") == "headset"
    assert suggest_type("Controller (Flydigi Direwolf 4)", "Microsoft") == "gamepad"
    assert suggest_type("随便一个名字", "") == "mouse"


def test_generic_name_detection():
    assert is_generic_name("HID Interface") is True
    assert is_generic_name("USB Input Device") is True
    assert is_generic_name("") is True
    assert is_generic_name("G98 PRO Keyboard") is False


def test_available_devices_lists_unregistered_receiver(registry):
    found = available_devices(RECEIVER, registry)
    assert [d.key for d in found] == ["258A:01BB"]
    assert found[0].vendor_specific is True


def test_available_devices_skips_already_configured(registry):
    registry.add_device(
        DeviceConfig(
            id="known-receiver",
            type="keyboard",
            display_name="已知接收器",
            match={"vid": 0x258A, "pid": 0x01BB},
        )
    )
    assert available_devices(RECEIVER, registry) == []


def test_group_covered_uses_match_rules(registry):
    registry.add_device(DeviceConfig(id="by-vid", type="mouse", display_name="V", match={"vid": 0x258A}))
    assert group_covered(RECEIVER, registry) is True


def test_available_devices_skips_generic_names(registry):
    infos = [hid(0x2BDF, 0x028A, 0x0081, "HID Interface"), hid(0x046D, 0xC232, 0x0001, "")]
    assert available_devices(infos, registry) == []
    # 手动列表要能看见它们，否则用户没法主动登记
    assert len(available_devices(infos, registry, include_generic=True)) == 2


def test_available_devices_respects_tombstone(registry):
    """用户删掉自动登记的设备后，不能下一轮扫描又被加回来。"""
    for device in available_devices(RECEIVER, registry):
        registry.add_device(build_device_config(device, device.default_id), persist=False)
    assert registry.remove_device("auto-258a-01bb", persist=False) is True
    assert available_devices(RECEIVER, registry) == []


def test_tombstoned_id_is_not_offered(registry):
    """墓碑按 id 生效：没有对应配置条目时也要挡住。"""
    registry._removed.append("auto-258a-01bb")
    registry._rebuild()

    assert available_devices(RECEIVER, registry) == []
    assert available_devices(RECEIVER, registry, include_removed=True)


def test_available_devices_skips_hardware_of_removed_device(registry):
    """删掉内置设备后，同一个接收器不该换个身份又被自动登记成新设备。"""
    registry.add_device(
        DeviceConfig(
            id="known-receiver",
            type="keyboard",
            display_name="已知接收器",
            match={"vid": 0x258A, "pid": 0x01BB},
        ),
        persist=False,
    )
    registry.remove_device("known-receiver", persist=False)

    assert registry.get("known-receiver") is None
    assert available_devices(RECEIVER, registry) == [], "删掉的是这台硬件，不是这条配置"


def test_available_devices_sorts_vendor_first(registry):
    infos = [
        hid(0x1234, 0x0001, 0x0001, "Plain Keyboard"),
        hid(0x258A, 0x01BB, 0xFF00, "2.4G Wireless Receiver"),
    ]
    found = available_devices(infos, registry)
    assert found[0].vendor_specific is True
    assert found[-1].key == "1234:0001"


def test_make_device_id_is_deterministic(registry):
    device = describe_group(RECEIVER)
    assert device.default_id == "auto-258a-01bb"
    assert make_device_id(device, registry) == "auto-258a-01bb"


def test_make_device_id_avoids_collision(registry):
    device = describe_group(RECEIVER)
    registry.add_device(DeviceConfig(id="auto-258a-01bb", type="mouse", display_name="占位"))
    assert make_device_id(device, registry) == "auto-258a-01bb-2"


def test_build_device_config_shape(registry):
    device = describe_group(RECEIVER)
    cfg = build_device_config(device, device.default_id)
    assert cfg.id == "auto-258a-01bb"
    assert cfg.source == SOURCE_AUTO
    assert cfg.match == {"vid": 0x258A, "pid": 0x01BB}
    assert cfg.protocol["name"] == "hid"
    assert cfg.type == "mouse"
    assert "自动登记" in cfg.notes


def test_build_manual_config_is_user_source():
    cfg = build_manual_config(0x046D, 0xC547, "旧鼠标", "mouse", "auto-046d-c547")
    assert cfg.source == SOURCE_USER
    assert cfg.display_name == "旧鼠标"
    assert cfg.match == {"vid": 0x046D, "pid": 0xC547}
    assert "手动添加" in cfg.subtitle


def test_auto_add_flow_end_to_end(registry, tmp_path):
    """模拟监测线程扫到设备 → 界面登记 → 下一轮不再重复上报。"""
    found = available_devices(RECEIVER, registry)
    for device in found:
        registry.add_device(build_device_config(device, device.default_id))

    assert registry.get("auto-258a-01bb") is not None
    assert DeviceRegistry(user_dir=tmp_path).get("auto-258a-01bb") is not None
    assert available_devices(RECEIVER, registry) == [], "登记之后不应再次上报"
