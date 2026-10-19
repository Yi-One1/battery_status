"""设备发现：把 HID 枚举结果聚合成"一台物理设备"，供新增列表与自动接入使用。

为什么必须聚合：一个 2.4G 接收器在 hidapi 里通常对应 3~6 个接口
（键盘页、鼠标页、Consumer 页、厂商页……），逐个列举用户根本分不清哪一行是哪台设备。
这里按 VID:PID 归并成一条，并标出它有没有厂商自定义接口（Usage Page ≥ 0xFF00）——
只有那一层才可能读得到电量。

聚合结果同时服务于两条路径：
  * 手动新增 —— 对话框里列出来，用户点一下即写入配置；
  * 自动新增 —— 监测线程每轮扫到"已经在用但没登记"的设备就通知主界面补一条。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .config import SOURCE_AUTO, SOURCE_USER, DeviceConfig, DeviceRegistry
from .hidapi_io import HidDeviceInfo

# 产品名 -> 设备类型的关键字表（顺序即优先级）
TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("keyboard", ("keyboard", "键盘", "keychron", "kbd", "g98", "87 key", "104 key")),
    ("headset", ("headset", "headphone", "earphone", "earbud", "audio", "耳机", "耳麦", "音箱", "speaker")),
    ("gamepad", ("gamepad", "game pad", "controller", "joystick", "手柄", "xbox", "dualshock", "8bitdo")),
    ("mouse", ("mouse", "鼠标", "mice", "trackball")),
)

VENDOR_PAGE_MIN = 0xFF00

# Windows 会给很多内部接口起这种毫无信息量的名字。自动新增必须跳过它们，
# 否则第一次启动就会冒出五六张"USB Input Device"的空卡片。
GENERIC_NAMES = (
    "hid interface",
    "usb input device",
    "input device",
    "hid-compliant",
    "符合 hid",
    "consumer control",
    "system control",
    "vendor-defined device",
    "供应商定义的设备",
)


def is_generic_name(name: str) -> bool:
    lowered = (name or "").strip().lower()
    if not lowered:
        return True
    return any(token in lowered for token in GENERIC_NAMES)


@dataclass(frozen=True)
class DiscoveredDevice:
    """一台被归并出来的物理设备。"""

    vid: int
    pid: int
    name: str
    manufacturer: str
    usage_pages: tuple[int, ...]
    interfaces: int
    vendor_specific: bool

    @property
    def key(self) -> str:
        return f"{self.vid:04X}:{self.pid:04X}"

    @property
    def display_name(self) -> str:
        return self.name or f"HID 设备 {self.key}"

    @property
    def default_id(self) -> str:
        """确定性 id —— 自动新增靠它去重，也靠它识别"用户删过"的墓碑。"""
        return f"auto-{self.vid:04x}-{self.pid:04x}"

    @property
    def suggested_type(self) -> str:
        return suggest_type(self.name, self.manufacturer)


def suggest_type(name: str = "", manufacturer: str = "") -> str:
    haystack = f"{name} {manufacturer}".lower()
    for type_key, keywords in TYPE_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return type_key
    return "mouse"


def group_infos(infos: Iterable[HidDeviceInfo]) -> dict[tuple[int, int], list[HidDeviceInfo]]:
    groups: dict[tuple[int, int], list[HidDeviceInfo]] = {}
    for info in infos:
        if not info.vid and not info.pid:
            continue
        groups.setdefault((int(info.vid), int(info.pid)), []).append(info)
    return groups


def describe_group(items: list[HidDeviceInfo]) -> DiscoveredDevice:
    vid, pid = int(items[0].vid), int(items[0].pid)
    name = next((i.product for i in items if i.product), "")
    maker = next((i.manufacturer for i in items if i.manufacturer), "")
    pages = tuple(sorted({int(i.usage_page) for i in items}))
    # hidapi 会把同一个 path 按顶层集合重复列出，接口数要按 path 去重才准
    paths = {i.path for i in items}
    return DiscoveredDevice(
        vid=vid,
        pid=pid,
        name=name,
        manufacturer=maker,
        usage_pages=pages,
        interfaces=len(paths),
        vendor_specific=any(page >= VENDOR_PAGE_MIN for page in pages),
    )


def group_covered(items: list[HidDeviceInfo], registry: DeviceRegistry) -> bool:
    """这组接口是否已经被"我们认得的某条配置"接走了。

    注意用的是 known_devices()（含墓碑），而不是当前生效的 devices：
    用户把某台设备删掉之后，硬件还是那台硬件，自动发现不该把它当新设备再登记一遍。
    """
    for cfg in registry.known_devices():
        if not cfg.match:
            continue
        if any(DeviceRegistry.matches(info, cfg.match) for info in items):
            return True
    return False


def available_devices(
    infos: Iterable[HidDeviceInfo],
    registry: DeviceRegistry,
    *,
    include_generic: bool = False,
    include_removed: bool = False,
) -> list[DiscoveredDevice]:
    """列出"能用但还没登记"的设备。

    include_generic  连没有产品名的标准 HID 集合也列出来（默认不列，太吵）
    include_removed  连用户删过的也列出来（默认不列，避免删完又自动加回来）
    """
    out: list[DiscoveredDevice] = []
    for items in group_infos(infos).values():
        device = describe_group(items)
        if not include_generic and not device.vendor_specific and is_generic_name(device.name):
            continue
        if group_covered(items, registry):
            continue
        if not include_removed and registry.is_removed(device.default_id):
            continue
        out.append(device)
    out.sort(key=lambda d: (0 if d.vendor_specific else 1, -d.interfaces, d.key))
    return out


def make_device_id(device: DiscoveredDevice, registry: DeviceRegistry, *, prefix: str = "") -> str:
    """生成不冲突的设备 id。

    刻意只用 VID:PID 推导（不掺产品名）：id 必须稳定，
    否则「用户删过这台设备」的墓碑下次就对不上了，删了又会自动加回来。
    """
    base = f"{prefix}{device.default_id}"
    if registry.get(base) is None:
        return base
    for index in range(2, 100):
        candidate = f"{base}-{index}"
        if registry.get(candidate) is None:
            return candidate
    return base


def build_device_config(
    device: DiscoveredDevice,
    device_id: str,
    *,
    display_name: Optional[str] = None,
    type_key: Optional[str] = None,
    source: str = SOURCE_AUTO,
) -> DeviceConfig:
    """把发现结果转成一条可直接入册的配置。

    协议先填 ``hid`` + 被动监听；能读到标准电量页就直接生效，
    读不到则卡片会显示"暂无电量"，用户可以再补 ``protocol.parse``。
    """
    return DeviceConfig(
        id=device_id,
        type=type_key or device.suggested_type,
        display_name=display_name or device.display_name,
        subtitle=f"{device.key} · HID",
        enabled=True,
        match={"vid": device.vid, "pid": device.pid},
        protocol={"name": "hid", "prefer": "auto", "init": [], "parse": None},
        options={},
        notes=(
            f"由设备发现自动登记（{device.interfaces} 个接口，"
            f"{'含' if device.vendor_specific else '不含'}厂商通道）。"
            "如需精确电量，请在该条目里补 protocol.parse 或改成 vendor_query。"
        ),
        source=source if source in (SOURCE_USER, SOURCE_AUTO) else SOURCE_USER,
    )


def build_manual_config(
    vid: int,
    pid: int,
    display_name: str,
    type_key: str,
    device_id: str,
    *,
    product: str = "",
) -> DeviceConfig:
    """手动按 VID/PID 新增（设备此刻不一定插着）。"""
    device = DiscoveredDevice(
        vid=int(vid),
        pid=int(pid),
        name=product or display_name,
        manufacturer="",
        usage_pages=(),
        interfaces=0,
        vendor_specific=False,
    )
    cfg = build_device_config(
        device,
        device_id,
        display_name=display_name or f"{vid:04X}:{pid:04X}",
        type_key=type_key,
        source=SOURCE_USER,
    )
    cfg.subtitle = f"{vid:04X}:{pid:04X} · 手动添加"
    cfg.notes = "手动添加的设备。若读不到电量，可在 devices.json 里补 protocol 段。"
    return cfg
