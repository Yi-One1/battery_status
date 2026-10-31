"""开机自启动：写 HKCU 的 Run 项，无需管理员权限。"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from .paths import executable_command

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "PowerHub"
# 改名前的旧值名：老用户注册表里还留着它，读到就视为「已开启」，
# 下次写入时顺手迁移到 VALUE_NAME 并删掉旧值，避免新旧两条同时自启。
LEGACY_VALUE_NAMES = ("PeripheralBattery",)


def _command_line() -> str:
    parts = executable_command()
    return " ".join(f'"{part}"' for part in parts) + " --tray"


def is_enabled() -> bool:
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            for name in (VALUE_NAME, *LEGACY_VALUE_NAMES):
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    continue
                if value:
                    return True
            return False
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.debug("查询自启动失败：%s", exc)
        return False


def apply(enabled: bool) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command_line())
                log.info("已开启开机自启：%s", _command_line())
                _drop_legacy(key, winreg)
            else:
                for name in (VALUE_NAME, *LEGACY_VALUE_NAMES):
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
                log.info("已关闭开机自启")
        return True
    except OSError as exc:
        log.error("写自启动注册表失败：%s", exc)
        return False


def _drop_legacy(key, winreg) -> None:
    """删掉改名前的旧自启值，避免新旧两条同时存在。"""
    for name in LEGACY_VALUE_NAMES:
        try:
            winreg.DeleteValue(key, name)
            log.info("已移除旧自启项：%s", name)
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.debug("移除旧自启项 %s 失败：%s", name, exc)


def open_task_manager_startup() -> None:
    """兜底：打开系统「启动应用」设置页。"""
    try:
        subprocess.Popen(["cmd", "/c", "start", "", "ms-settings:startupapps"], shell=False)
    except Exception:  # noqa: BLE001
        pass


def shortcut_target() -> Path:
    parts = executable_command()
    return Path(parts[0])
