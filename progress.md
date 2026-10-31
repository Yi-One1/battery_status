# 项目进展（progress）

> 更新：2026-11-01　版本：**v1.7.0**（`dist/电量中枢_PowerHub_v1.7.0.exe`）　状态：`main` 已推 origin/main

## 已完成

| 事项 | 提交 |
|---|---|
| 托盘图标排版优化：数字 9.4→11.6px 并占满徽标，充电标记改为右下角绿点（不再挤数字） | `b830d51` |
| 设备排序改为**拖拽**（设置页设备行 + 主界面卡片都有 ⋮⋮ 手柄），托盘跟随重排 | `82c252a` |
| 修复托盘图标顺序「完全反序」（Windows 把新注册图标插在最前 → 改逆向注册） | `6018db2` |
| 断连收起时长支持 **1 秒 ~ 1 小时**（数值输入 + 单位菜单，去掉了固定档位） | `cf25ccc` `e5a0703` |
| 修复浅色设备颜色下托盘数字看不清（按底色亮度自动切黑/白字） | `380c961` |
| 修复托盘图标顺序与主界面不一致、每次重启还变（启动即建齐 + 逐个注册） | `6db982f` |
| 版本号体系：`APP_VERSION` + 产物名 `电量中枢_PowerHub_v<版本>.exe`，旧产物一律保留 | `bc0f4fa` |
| 修复老用户配置冻结：内置 `devices.json` 的 `version` 一变，用户层内置副本的 `protocol` 段整体按内置刷新 | `018708d` |
| 开机自启注册表值名 `PeripheralBattery` → `PowerHub`，带旧值迁移 | `bd1a72c` |
| 软件更名「电量中枢 / PowerHub」，源码/文档/测试断言全同步 | `5fc27f8` |
| 迈从 G98 Pro：充电标志位改为 `byte6`（有线 `byte9`）的 **bit4** | `b3f36d9` |
| 迈从 G98 Pro：删除误配的 `charging_invert`（曾导致未充电误报充电中） | `408f05e` |
| HECATE G3 MAX：补充电位 `byte7` 的 **bit0**，修复充电不显示「充电中」 | `face728` |

测试：**370 passed / 1 skipped**（skip 为真机无应答的 live 用例）。

## 关键决策

- **版本号**：唯一来源 `app/core/paths.py: APP_VERSION`。修 bug 升末位、加功能升中位、不兼容升首位。
  **只有内置设备协议变更时**才同步升 `app/config/devices.json` 的 `version`（它是触达老用户协议刷新的开关）。
- **打包产物**：`电量中枢_PowerHub_v<版本>.exe`，PyInstaller 先用 ASCII 名打包再重命名。
  同版本重打属于重建（会覆盖同名文件），跨版本产物天然不同名。**旧版本一律保留**，删不删由使用者决定。
- **断连收起**：内部按秒存（`offline_hide_seconds`，1~3600），旧配置 `offline_hide_minutes` 自动 ×60 迁移。
  控件是「数值 + 单位菜单」，数值与单位是一个整体输入（10 分钟切到「秒」= 10 秒），越界自动夹紧；
  单位用 `QToolButton + QMenu` 而不是 `QComboBox`（后者弹出列表会先闪一帧黑底）。
- **托盘数字对比度**：`foreground_on()` 按 WCAG 相对亮度、阈值 0.5 选黑/白，浅色设备色不再白字白底。
- **托盘图标排版**：数字模式就是一整块圆角徽标（内缩 0.5、几乎占满 16px 画布），数字 11.6px 加粗居中，
  三位数自动收字号到 9.0；充电用右下角小绿点角标，**数字字号与位置不因充电变化**。
- **设备排序**：设置页设备行与主界面卡片都用「⋮⋮ 手柄拖动」（`app/ui/drag_sort.py`），
  不用整行拖动 —— 设置页每行里嵌着输入框/取色/开关，整行拖会抢鼠标。
  拖完写 `device_order`，主界面、托盘（`ordered_devices` 同一口径）一起重排。
- **托盘顺序**：启动即按主界面顺序把槽位建齐（未扫到的先灰电池占位），扫描回来**只换图不重建**；
  Windows 把新注册图标插在最前，所以注册要**按显示顺序倒序**（`_do_swap` 里 `reversed`）并逐个注册
  （`CREATE_INTERVAL_MS`）。
- **命名**：中文「电量中枢」/ 英文 `PowerHub`；内部标识 `APP_NAME` 保留 `BatteryGlass`（配置目录 `%APPDATA%\BatteryGlass` 不变 → 零迁移、不丢用户数据）。
- **电量口径**：以硬件原始值为准（如 83%），不对齐官方驱动的取整/平滑。
- **充电时的电量**：硬件只报 50% 占位符、无真实值 → 显示「充电中 + 闪电图标」，绝不显示 50。
- **配置三层**：内置 `app/config/devices.json` / 用户层 `%APPDATA%\BatteryGlass\devices.json` / `dist/*/_internal/config/devices.json`。
- **配置合并规则**：内置为底、用户层覆盖；内置设备在用户层的副本带 `based_on`，版本不一致时 `protocol` 段整体改用内置。
- **协议事实**：迈从充电位 = `byte6`（有线 `byte9`）bit4，`0x10`=充电中；HECATE 充电位 = `byte7` bit0，`0x01`=充电中。均正向，不取反。

## 未完成待办

- [ ] **部署新版本**（使用者选择暂不部署，产物只留在 `dist/`）：把新 exe 放到 `D:\桌面\APP\`，
      进设置页把开机自启关一次再开一次，注册表会自动从旧值 `PeripheralBattery` 迁到 `PowerHub`
- [ ] （可选）`dist/` 下的历史产物要不要清理，交由使用者决定

## 接续要点

- 跑测试：`./.venv/Scripts/python.exe -m pytest tests --basetemp=.pytest-tmp`
  （pytest 临时目录偶被占用，用 `--basetemp` 绕开；`pytest.ini` 已含 `-q`，别再重复加）
- 打包：`./.venv/Scripts/python.exe tools/build_exe.py`（单文件）/ `--onedir`（目录版）；
  **venv 重建后需补装 PyInstaller**；目标 exe 被占用时先退出软件
- 托盘相关测试：需要把 `SWAP_DELAY_MS`、`CREATE_INTERVAL_MS` 都归零，再用「空转事件循环」
  （`for _ in range(40): qapp.processEvents()`）把两轮定时器跑完，否则只建成第一枚
- 关键文件：`app/core/paths.py`（名称/版本）、`app/config/devices.json`（设备规则 + 配置版本）、
  `app/core/config.py`（三层合并 + 协议刷新）、`app/core/presence.py`（断连收起判定）、
  `app/core/protocols.py`（协议解析）、`app/core/model.py`（显示逻辑）、
  `app/ui/tray.py`（托盘图标绘制与槽位管理）、`app/ui/settings.py`（设置页）、`app/core/autostart.py`（自启 + 旧值迁移）
- 长期记忆：`.workbuddy/memory/MEMORY.md`；历史日志：`.workbuddy/memory/2026-1*.md`
