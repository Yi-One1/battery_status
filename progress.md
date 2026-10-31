# 项目进展（progress）

> 更新：2026-11-01　版本：**v1.4.0**（`dist/电量中枢_PowerHub_v1.4.0.exe`）　状态：`main` 领先远端 3 个提交

## 已完成

| 事项 | 提交 |
|---|---|
| 版本号体系：`APP_VERSION` + 产物名 `电量中枢_PowerHub_v<版本>.exe`，旧产物一律保留 | `HEAD` |
| 修复老用户配置冻结：内置 `devices.json` 的 `version` 一变，用户层内置副本的 `protocol` 段整体按内置刷新（保留显示名等自定义，清掉内置已删的键） | `HEAD` |
| 开机自启注册表值名 `PeripheralBattery` → `PowerHub`，带旧值迁移（读到旧值视为已开启，写入时迁走并删除） | `HEAD` |
| 测试隔离：conftest 把默认用户目录指向临时目录，避免测试写坏真实 `%APPDATA%\BatteryGlass\devices.json` | `HEAD` |
| 软件更名「电量中枢 / PowerHub」，源码/文档/测试断言全同步 | `5fc27f8` |
| 迈从 G98 Pro：充电标志位改为 `byte6`（有线 `byte9`）的 **bit4**；有线通道 `charging:true` 落到 Reading | `b3f36d9` |
| 迈从 G98 Pro：删除误配的 `charging_invert`（曾导致未充电误报充电中） | `408f05e` |
| HECATE G3 MAX：补充电位 `byte7` 的 **bit0**，修复充电不显示「充电中」 | `face728` |

测试：**324 passed / 1 skipped**（skip 为真机无应答的 live 用例），全量跑完真实用户配置哈希不变。

## 关键决策

- **版本号**：唯一来源 `app/core/paths.py: APP_VERSION`。修 bug 升末位、加功能升中位、不兼容升首位。
  每次改动都要先升版本再打包，并同步 `app/config/devices.json` 的 `version`（它是触达老用户协议刷新的开关）。
- **打包产物**：`电量中枢_PowerHub_v<版本>.exe`，PyInstaller 先用 ASCII 名 `PowerHub_v<版本>` 打包、再重命名，
  规避非 ASCII 文件名在工具链上的编码问题。同版本重打属于重建（会覆盖同名文件），跨版本产物天然不同名。
- **旧产物不删**：`dist/` 下历史产物（含 `PowerHub.exe`、`设备电量显示.exe`、`设备电量显示/` 目录版）全部保留，
  是否删除由使用者决定。⚠️ 目录版 `dist/设备电量显示/` 是旧代码 + 旧设备配置（迈从充电位还是 bit0），**不要直接使用**。
- **命名**：中文「电量中枢」/ 英文 `PowerHub`；内部标识 `APP_NAME` 保留 `BatteryGlass`（配置目录 `%APPDATA%\BatteryGlass` 不变 → 零迁移、不丢用户数据）。
- **电量口径**：以硬件原始值为准（如 83%），不对齐官方驱动的取整/平滑。
- **充电时的电量**：硬件只报 50% 占位符、无真实值 → 显示「充电中 + 闪电图标」，绝不显示 50。
- **配置三层**：内置 `app/config/devices.json` / 用户层 `%APPDATA%\BatteryGlass\devices.json` / `dist/*/_internal/config/devices.json`。改设备规则必须三处同步，否则被旧层盖回。
- **配置合并规则**：内置为底、用户层覆盖；内置设备在用户层的副本带 `based_on`（快照对应的内置 `version`），
  版本不一致时 `protocol` 段整体改用内置（顺带清掉内置已删除的键），其余字段保留用户值。
- **协议事实**：迈从充电位 = `byte6`（有线 `byte9`）bit4，`0x10`=充电中；HECATE 充电位 = `byte7` bit0，`0x01`=充电中。均正向，不取反。

## 未完成待办

- [ ] **推送**：`git push`（本地已 3 个提交待推；agent 侧 GitHub 通道不可达 `Empty reply from server`，需本机执行）
- [ ] **部署新版本**（使用者选择暂不部署，产物只留在 `dist/`）：实际在跑的是 `D:\桌面\APP\设备电量显示.exe`（10-22 旧版），
      自启注册表旧值 `PeripheralBattery` 指向它。换用时把新 exe 放到 `D:\桌面\APP\`，进软件设置页关一次自启再开一次，
      新值 `PowerHub` 自动写入、旧值自动删除
- [ ] （可选）`dist/设备电量显示/` 目录版要不要清理，交由使用者决定

## 接续要点

- 跑测试：`./.venv/Scripts/python.exe -m pytest tests --basetemp=.pytest-tmp`（pytest 临时目录偶被占用，用 `--basetemp` 绕开；`pytest.ini` 已含 `-q`，别再重复加）
- 打包：`./.venv/Scripts/python.exe tools/build_exe.py`（单文件）/ `--onedir`（目录版）；**venv 重建后需补装 PyInstaller**；目标 exe 被占用时先退出软件
- 关键文件：`app/core/paths.py`（名称/版本常量）、`app/config/devices.json`（设备规则 + 配置版本号）、`app/core/config.py`（三层合并 + 协议刷新）、`app/core/protocols.py`（协议解析）、`app/core/model.py`（显示逻辑）、`app/core/autostart.py`（自启 + 旧值迁移）
- 长期记忆：`.workbuddy/memory/MEMORY.md`；历史日志：`.workbuddy/memory/2026-10-2*.md`
