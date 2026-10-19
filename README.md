# 外设电量 · PeripheralBattery

Windows 桌面端 2.4G 无线外设电量监测工具。常驻托盘，实时显示飞智冰原狼 4、迈从 G98 PRO、
罗技 GPW2、漫步者 G3 MAX 的电量与状态；界面为苹果风柔光玻璃；设备接入完全配置化，
新增型号不需要改代码。

<p align="center">
  <img src="preview/panel-demo.png" width="700" alt="主面板">
</p>

<p align="center">
  <img src="preview/settings-demo.png" width="345" alt="设置页">
  <img src="preview/tray_icons.png" width="345" alt="托盘图标">
</p>

---

## 一、当前实测状态（看这张表比看宣传语有用）

| 外设 | 识别方式 | 电量来源 | 实测结果 |
| --- | --- | --- | --- |
| 罗技 GPW2 | `046D:C547` LIGHTSPEED 接收器 | HID++ 2.0 `0x1004` 统一电量，长报文 `0x11`，设备索引 1 | ✅ 正常，读到 61%（真实值） |
| 飞智 冰原狼 4 | `045E:028E` XInput 兼容手柄 | `XInputGetBatteryInformation` | ⚠️ 只给四档（5/25/60/100）；有线供电时无档位，显示「充电中 / --」 |
| 迈从 G98 PRO | `258A:01BB` SINOWEALTH 2.4G 接收器 | 厂商私有报文（待抓包） | ⚠️ 不暴露标准电量页，需按第五节配置 `parse` 规则 |
| 漫步者 G3 MAX | `35BB:A226` Jieli 2.4G dongle | 厂商私有报文（待抓包） | ⚠️ 同上 |

说明：三台厂商 dongle 都是"私有协议"，没有任何公开文档，**唯一可靠的办法是在真机上抓报文**。
本项目把这件事做成了"改配置"，而不是"改代码"，并提供抓包工具（`tools/usb_probe.py`）。
另外，罗技鼠标休眠时不会应答 HID++ 请求，界面会显示「休眠中」并保留上次电量，动一下鼠标即可刷新。

---

## 二、整体方案

参考 [USB Device Viewer](https://www.codertools.net/tools/usb-device-viewer.php?lang=zh) 的思路：
**先把设备枚举清楚，看清它暴露了哪些 HID Usage，再决定用哪种协议去读**。
所以整个程序是"枚举 → 匹配 → 协议 → 状态 → 呈现"的一条流水线，每层都可替换。

```
┌──────────────────────────── UI 层（PySide6 / 全自绘） ────────────────────────────┐
│  托盘图标（每设备一枚，动态绘制）   玻璃面板（卡片 / 环形电量）   设置页（颜色·样式）  │
└───────────────────────────────▲──────────────────────────────────────────────────┘
                                │ Signal(dict[device_id, Snapshot])
┌───────────────────────────────┴──────── 监测线程（单个 QThread） ─────────────────┐
│  定时唤醒 → 枚举 HID → 按设备匹配 → 逐台读数 → 合并历史/离线/休眠 → 广播快照        │
└───────────────────────────────▲──────────────────────────────────────────────────┘
                                │ Reading(level, charging, detail, source)
┌───────────────────────────────┴──────── 协议层（可插拔） ─────────────────────────┐
│  logitech_hidpp：HID++ 2.0（0x1004 / 0x1000 / 0x1001 / 0x0005）                    │
│  xinput        ：Windows XInput 电池 API（四档）                                   │
│  hid           ：标准 HID Battery Strength → 回退到配置驱动的厂商报文解析            │
│  demo          ：演示数据                                                          │
└───────────────────────────────▲──────────────────────────────────────────────────┘
                                │ 字节流
┌───────────────────────────────┴──────── 设备层 ──────────────────────────────────┐
│  hidapi（枚举/读写） + hid.dll HidP_*（报告描述符、Usage 解析、Usage 取值）           │
└──────────────────────────────────────────────────────────────────────────────────┘
```

数据流很短：**设备 → Reading → Snapshot → 像素**。任何一层出问题都不会拖垮别的设备，
单台设备读取异常只会让它自己那张卡片变成"未连接 / 暂无电量"，并附带原因。

---

## 三、关键技术点

### 1. 设备枚举与"看清 Usage"
`hidapi` 负责拿到 `path / vid / pid / usage_page / usage`，`hid.dll` 的
`HidD_GetPreparsedData` + `HidP_GetValueCaps` 负责把「这个接口暴露了哪些 Usage」列出来，
`HidP_GetUsageValue` 再按 Usage 从原始报文里取值。这样标准电量页
（Usage Page `0x06` / Usage `0x20` Battery Strength）不需要手写位偏移就能读。

> 踩坑记录：`HidP_*` 的返回值是 NTSTATUS 风格，成功是 `0x00110000` 而不是 `0`，
> 用 `!= 0` 判断会把所有设备都判成失败。

### 2. 罗技 HID++ 2.0（本项目最硬的一段）
- 接收器上会同时出现 **7 字节**和 **20 字节** 两个 vendor 接口。
  20 字节接口必须发 **长报文 `0x11`**、7 字节接口发 **短报文 `0x10`**，混用会被系统直接拒绝（错误 5 / 错误 1）。
- 新固件的 GPW2 没有 `0x1000`，只有 `0x1004 UNIFIED_BATTERY`：
  先用 root `0x0000` 的 `getFeature(0x1004)` 换到 feature index（本例是 6），再调 `fn1 getStatus`，
  返回 `[电量%, 电量状态, 充电类型, 外部供电]`，充电类型 `0` 表示放电中。
- 设备索引不写死：遍历 1-6 用 `0x0003` 探活，再用 `0x0005` 读设备名做型号匹配；
  索引 0xFF 作为有线模式兜底。索引探测结果缓存 5 分钟，避免每轮都全表试探。
- **休眠与离线必须区分**：休眠是"没应答"（Timeout），未配对是"错误码 0x08"。
  两者在界面上是不同文案，否则用户会以为设备掉了。

### 3. XInput 手柄电量
`XInputGetBatteryInformation` 只返回 4 档 + 电池类型。`BATTERY_TYPE_WIRED` 时 Windows 固定报"满"，
**不能当成 100%**，本项目此时返回"充电中 + 无档位"。四档折算为 5/25/60/100。

### 4. 配置驱动的厂商报文解析（扩展性的核心）
`devices.json` 里每台设备描述"怎么找到它"和"怎么读它"：

```json
{
  "id": "mchose-g98pro",
  "type": "keyboard",
  "display_name": "迈从 G98 PRO",
  "match": { "vid": 9610, "pid": 443, "product_contains": ["2.4g wireless receiver"] },
  "protocol": {
    "name": "hid",
    "init": ["07 02 00 00"],                       // 可选：先发唤醒/查询报文
    "parse": {                                     // 从上报里取电量
      "prefix": "5A A5", "mask": "FF FF",          // 前缀 + 掩码匹配
      "offset": 4, "scale": 1,                     // 第 4 字节即电量
      "charging_byte": 5, "charging_bit": 1
    }
  }
}
```

`match` 的四个键（`vid` / `pid` / `usage_page` / `product_contains`）任意组合，
省略即不参与过滤。用户还可以在 `%APPDATA%\BatteryGlass\devices.json` 里追加或覆盖同名条目，
**不动安装目录**，升级不会丢配置。

### 5. 低占用是怎么做到的
- 单个 `QThread` 串行扫描，默认 **60 秒**一次；扫描完 `Event.wait(interval)` 睡死，不占 CPU。
- 读完立刻 `close()` 句柄，不长期占用 USB 接口；HID++ 索引缓存把每轮 USB 往返从 ~20 次降到 3~4 次。
- 托盘图标、面板控件全部按需重绘，没有常驻动画；窗口隐藏后不做任何绘制。
- 实测常驻内存约 60-80 MB（PySide6 运行时为主），空闲 CPU 占用 ≈ 0%。
- 手动刷新、USB 插拔（`WM_DEVICECHANGE` 广播）用事件立即唤醒，不做忙轮询。

### 6. 苹果风柔光玻璃（不靠图片）
全部 `QPainter` 自绘：纵向白色渐变底 → 左上径向柔光 → 64×64 噪点纹理（磨砂颗粒）→
顶部亮、底部淡的 1px 描边 → 大圆角 + `QGraphicsDropShadowEffect` 柔和外阴影。
另提供可选的 **DWM Acrylic**（`SetWindowCompositionAttribute`）真实磨玻璃，
开启时自动降低自绘底色透明度让桌面透上来；驱动不支持则静默降级。
电量控件有 **环形 / 条形 / 数字** 三种形态，颜色和形态都按"外设类型"分别配置。

### 7. 托盘与自启动
- 托盘图标是**代码绘制**的电池：颜色 = 该类型自定义色，填充量 = 电量，充电叠加白色闪电，
  低于 10% 整枚转红。支持"每设备一枚"与"合并一枚"两种模式（合并时取最低电量）。
- 图标按 16/18/20/24/32/40/48/64 多尺寸渲染，避免 DPI 缩放发虚。
- 开机自启写 `HKCU\...\CurrentVersion\Run`（免管理员），启动参数 `--tray` 直接静默进托盘。
- `QLocalServer` 单实例：重复启动只唤起已有面板，不会开第二个监测线程。

---

## 四、目录结构

```
外设电量显示软件_DeepSeek版/
├─ main.py                  入口：单实例、参数、装配
├─ run.bat                  一键启动（首次自动建 venv 装依赖）
├─ requirements.txt
├─ app/
│  ├─ config/devices.json   ★ 设备清单（新增型号改这里）
│  ├─ core/
│  │  ├─ paths.py           源码/打包两种运行环境的路径
│  │  ├─ config.py          设备注册表 + 偏好设置持久化
│  │  ├─ model.py           Reading / Snapshot 数据模型
│  │  ├─ hidapi_io.py       hidapi 枚举与句柄（带超时）
│  │  ├─ hidnative.py       hid.dll：Value Caps、Usage 取值、报告描述符
│  │  ├─ protocols.py       HID++ / 通用 HID / 厂商报文
│  │  ├─ xinput.py          XInput 电量
│  │  ├─ monitor.py         后台监测线程
│  │  ├─ autostart.py       开机自启注册表
│  │  └─ demo.py            演示数据
│  └─ ui/
│     ├─ theme.py           颜色/字体/全局 QSS
│     ├─ glass.py           柔光玻璃绘制、开关、分段控件、矢量图标按钮
│     ├─ gauge.py           环形/条形/数字电量控件
│     ├─ cards.py           设备卡片
│     ├─ settings.py        设置页
│     ├─ panel.py           主面板（无边框 + 拖动 + 热插拔监听）
│     ├─ tray.py            托盘图标与菜单
│     └─ winblur.py         DWM Acrylic
└─ tools/
   ├─ selfcheck.py          ★ 一键自检（环境/配置/逐台读数）
   ├─ usb_probe.py          ★ 设备诊断/抓包（接入新设备用）
   ├─ make_icon.py          生成 app.ico
   ├─ screenshot.py         界面预览图（--live 用真实设备数据）
   └─ build_exe.py          PyInstaller 打包
```

---

## 五、怎么新增一台外设（三步）

**第 1 步：看清设备。**

```bat
.venv\Scripts\python.exe tools\usb_probe.py list
```

输出里找你的设备，记下 `VID:PID` 和 `UP`（Usage Page）。若"电量页"一列是 ✔，直接跳到第 3 步，
配置 `"protocol": {"name": "hid"}` 即可。

**第 2 步：抓报文。** 先把接收器插好、设备开机，然后：

```bat
.venv\Scripts\python.exe tools\usb_probe.py watch --vid 0x258A --pid 0x01BB
```

工具会打印去重后的报文，并给出"值在 1-100 且稳定"的候选字节。抓的时候最好操作一下设备
（按键、切模式），让厂商协议主动上报。若设备需要先被查询才回复，可以用 `--init "07 02 00 00"` 试探。

**第 3 步：写进配置。** 在 `app/config/devices.json` 的 `devices` 数组里加一条
（或写到 `%APPDATA%\BatteryGlass\devices.json` 覆盖），保存后点面板右上角刷新即可，
**不用重启、不用改代码**：

```json
{
  "id": "my-keyboard",
  "type": "keyboard",
  "display_name": "我的键盘",
  "match": { "vid": 9610, "pid": 443 },
  "protocol": {
    "name": "hid",
    "init": [],
    "parse": { "prefix": "", "offset": 3, "scale": 1 }
  }
}
```

`type` 决定它的颜色与显示样式（在设置页里逐类调整），也可以直接在 `types` 里新增一个分类。

---

## 六、运行与打包

```bat
:: 0) 先自检一眼（不弹界面）
.venv\Scripts\python.exe tools\selfcheck.py

:: 1) 直接跑（首次会自动建虚拟环境）
run.bat

:: 2) 手动跑
.venv\Scripts\python.exe main.py          :: 启动并弹出面板
.venv\Scripts\pythonw.exe main.py --tray  :: 静默进托盘（开机自启用这个）
.venv\Scripts\python.exe main.py --demo   :: 演示数据，先看界面

:: 3) 打包成 exe
.venv\Scripts\python.exe -m pip install pyinstaller
.venv\Scripts\python.exe tools\build_exe.py            :: 单文件 dist\外设电量.exe
.venv\Scripts\python.exe tools\build_exe.py --onedir   :: 目录版，冷启动更快
```

用户配置与日志在 `%APPDATA%\BatteryGlass\`（`preferences.json` / `devices.json` / `app.log`）。
卸载直接删目录即可，自启动项在设置里关掉或从任务管理器「启动应用」里禁用。

---

## 七、已知限制

1. **私有协议无文档**：迈从、漫步者的 dongle 走厂商自定义报文，必须真机抓包才能定位电量字节；
   本项目提供工具与配置位，但不猜测协议（乱写 Feature 报文有改坏设备配置的风险）。
2. 罗技鼠标休眠时无应答，属协议行为；电量刷新依赖设备被唤醒。
3. XInput 只有 4 档精度；有线供电时无档位可用。
4. 若同时装了 Logitech G HUB，个别情况下 HID++ 接口会被独占，表现为读取失败（日志里可见）。
5. 表格里的 `type` 颜色/样式改完即时生效；但更换设备清单（增删设备）需要重启程序。

---

## 八、还可以往哪走

- 给未支持设备加一个"校准向导"：面板内引导抓包 → 自动生成 `parse` 配置。
- 电量历史落盘（SQLite），面板里画 7 天曲线，顺便估算剩余可用时长。
- 用 `--low-power` 模式：面板隐藏时 5 分钟一次，面板打开时 10 秒一次。
- 备选协议：BLE HID（`Windows.Devices.Bluetooth`）/ 罗技 `0x1001` 电压曲线精修。
