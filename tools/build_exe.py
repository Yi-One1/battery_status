"""打包成免安装 exe（PyInstaller）。

    python tools/build_exe.py                # 单文件 exe，输出 dist/设备电量显示.exe
    python tools/build_exe.py --onedir       # 目录版，冷启动更快
    python tools/build_exe.py --name MyName  # 自定义可执行文件名

注意：单文件模式每次启动都会把运行库解压到临时目录，冷启动大约 1.5-3 秒；
长期开机自启建议用 --onedir + 快捷方式，或用源码 + pythonw 启动。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="打包设备电量显示")
    parser.add_argument("--name", default="设备电量显示")
    parser.add_argument("--onedir", action="store_true", help="生成目录版而不是单文件")
    parser.add_argument("--keep-build", action="store_true", help="保留 build 中间目录")
    args = parser.parse_args()

    if not (ROOT / "app" / "assets" / "app.ico").exists():
        print("· 未发现图标，先生成 app.ico")
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)

    # 上次的产物会让 PyInstaller 的清理步骤失败，先自己删干净
    target = ROOT / "dist" / (args.name + ("" if not args.onedir else ""))
    if target.exists():
        print("· 清理上次产物：", target)
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        else:
            target.unlink(missing_ok=True)

    add_data = f"{ROOT / 'app' / 'config'}{';' if sys.platform == 'win32' else ':'}config"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(ROOT / "main.py"),
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        args.name,
        "--icon",
        str(ROOT / "app" / "assets" / "app.ico"),
        "--add-data",
        add_data,
        "--collect-submodules",
        "app",
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "build"),
        "--specpath",
        str(ROOT / "build"),
    ]
    command.append("--onedir" if args.onedir else "--onefile")

    print("· 开始打包：", " ".join(command))
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        print("× 打包失败，请检查上面的输出")
        return result.returncode

    if not args.keep_build:
        shutil.rmtree(ROOT / "build", ignore_errors=True)

    print(f"\n✓ 完成，产物在 {ROOT / 'dist'}")
    print("  首次运行若被 Windows Defender 拦截，选择「仍要运行」即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
