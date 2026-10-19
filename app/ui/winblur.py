"""Windows DWM 毛玻璃（Acrylic）。失败时静默降级为自绘玻璃。"""
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes as W

log = logging.getLogger(__name__)

ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
WCA_ACCENT_POLICY = 19
DWM_BB_ENABLE = 0x1


class ACCENTPOLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_uint),
        ("AccentFlags", ctypes.c_uint),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_uint),
    ]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_uint),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


def _abgr(color: str, alpha: int) -> int:
    r = int(color[1:3], 16)
    g = int(color[3:5], 16)
    b = int(color[5:7], 16)
    return (alpha << 24) | (b << 16) | (g << 8) | r


def apply_acrylic(hwnd: int, tint: str = "#F5F7FB", alpha: int = 0x99, acrylic: bool = True) -> bool:
    """给窗口加上 DWM 模糊背景，颜色为 ABGR 的 GradientColor。"""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        setter = user32.SetWindowCompositionAttribute
    except Exception:  # noqa: BLE001
        return False

    policy = ACCENTPOLICY()
    policy.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND if acrylic else ACCENT_ENABLE_BLURBEHIND
    policy.AccentFlags = 2
    policy.GradientColor = _abgr(tint, alpha)
    policy.AnimationId = 0

    data = WINDOWCOMPOSITIONATTRIBDATA()
    data.Attribute = WCA_ACCENT_POLICY
    data.Data = ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p)
    data.SizeOfData = ctypes.sizeof(policy)
    try:
        setter.argtypes = [W.HWND, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
        setter.restype = W.BOOL
        return bool(setter(W.HWND(hwnd), ctypes.byref(data)))
    except Exception as exc:  # noqa: BLE001
        log.debug("设置 Acrylic 失败：%s", exc)
        return False


def clear_acrylic(hwnd: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        policy = ACCENTPOLICY()
        policy.AccentState = 0  # ACCENT_DISABLED
        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = WCA_ACCENT_POLICY
        data.Data = ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(policy)
        user32.SetWindowCompositionAttribute.argtypes = [W.HWND, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
        user32.SetWindowCompositionAttribute.restype = W.BOOL
        return bool(user32.SetWindowCompositionAttribute(W.HWND(hwnd), ctypes.byref(data)))
    except Exception:  # noqa: BLE001
        return False
