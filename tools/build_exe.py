"""打包成免安装 exe（PyInstaller）。

产物命名 = 中文名 + 英文名 + 版本号，例如 ``dist/电量中枢_PowerHub_v1.4.0.exe``。
版本号取自 ``app/core/paths.py`` 的 ``APP_VERSION``；每次改功能/修协议先升版本号再打包，
不同版本的产物互不覆盖，旧版本一律保留（是否删除由使用者决定）。

    python tools/build_exe.py                # 单文件 exe
    python tools/build_exe.py --onedir       # 目录版，冷启动更快
    python tools/build_exe.py --name MyName  # 自定义可执行文件名（不含版本号时自动补）

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
sys.path.insert(0, str(ROOT))

from app.core.paths import APP_DISPLAY_NAME, APP_EN_NAME, APP_VERSION  # noqa: E402

# PyInstaller 以纯 ASCII 名打包（规避非 ASCII 路径在部分工具链上的编码问题），
# 打完再重命名成中文+英文+版本号。
BUILD_NAME = f"{APP_EN_NAME}_v{APP_VERSION}"
FINAL_STEM = f"{APP_DISPLAY_NAME}_{APP_EN_NAME}_v{APP_VERSION}"


def main() -> int:
    parser = argparse.ArgumentParser(description=f"打包{APP_DISPLAY_NAME}（v{APP_VERSION}）")
    parser.add_argument("--name", default=FINAL_STEM, help="最终产物名（不含扩展名）")
    parser.add_argument("--onedir", action="store_true", help="生成目录版而不是单文件")
    parser.add_argument("--keep-build", action="store_true", help="保留 build 中间目录")
    args = parser.parse_args()

    if not (ROOT / "app" / "assets" / "app.ico").exists():
        print("· 未发现图标，先生成 app.ico")
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)

    dist = ROOT / "dist"
    ascii_target = dist / (BUILD_NAME + (".exe" if not args.onedir else ""))
    final_target = dist / (args.name + ("" if args.onedir else ".exe"))

    # 只清理「本次要生成的那个文件」：同版本重打属于重建，跨版本产物天然不同名，一律保留
    for stale in (ascii_target, final_target):
        if stale.exists():
            print("· 重建同版本产物：", stale)
            if stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)
            else:
                stale.unlink(missing_ok=True)

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
        BUILD_NAME,
        "--icon",
        str(ROOT / "app" / "assets" / "app.ico"),
        "--add-data",
        add_data,
        "--collect-submodules",
        "app",
        "--distpath",
        str(dist),
        "--workpath",
        str(ROOT / "build"),
        "--specpath",
        str(ROOT / "build"),
    ]
    command.append("--onedir" if args.onedir else "--onefile")

    print(f"· 版本：{APP_DISPLAY_NAME} {APP_EN_NAME} v{APP_VERSION}")
    print("· 开始打包：", " ".join(command))
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        print("× 打包失败，请检查上面的输出")
        return result.returncode

    # 重命名成 中文名_英文名_v版本号
    if ascii_target.resolve() != final_target.resolve():
        if final_target.exists():
            final_target.unlink(missing_ok=True)
        ascii_target.rename(final_target)
        print(f"· 已重命名：{ascii_target.name} → {final_target.name}")

    if not args.keep_build:
        shutil.rmtree(ROOT / "build", ignore_errors=True)

    print(f"\n✓ 完成，产物：{final_target}")
    kept = sorted(p.name for p in dist.iterdir() if p.name != final_target.name)
    if kept:
        print("  同目录下的历史产物（未删除）：" + "、".join(kept))
    print("  首次运行若被 Windows Defender 拦截，选择「仍要运行」即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
