"""路径与运行环境：区分源码运行 / PyInstaller 打包运行。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "BatteryGlass"
APP_DISPLAY_NAME = "设备电量显示"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """只读资源根目录（打包后位于 _MEIPASS）。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[1]


def project_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def bundled_config_dir() -> Path:
    return resource_root() / "config"


def user_data_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_path() -> Path:
    return user_data_dir() / "app.log"


def executable_command() -> list[str]:
    """返回可用于开机自启的启动命令。"""
    if is_frozen():
        return [str(Path(sys.executable).resolve())]
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else python
    entry = project_root() / "main.py"
    return [str(exe), str(entry)]
