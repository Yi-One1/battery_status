"""在 API 层追踪厂商驱动与设备之间的 HID 通信（Windows / Frida）。

用途：厂商私有协议没有文档时，与其盲猜报文，不如让驱动自己去读，
       我们在 `hidapi` / 厂商 SDK 的导出函数上挂一个观察点，直接看它发了什么、收到什么。

    python tools/hid_trace.py --name HECATE.exe --seconds 12
    python tools/hid_trace.py --pid 12844 --seconds 12 --show-all
    python tools/hid_trace.py --name "MCHOSE HUB.exe" --seconds 12

注意：只读观察，不修改任何报文；注入的是独立进程，出问题不会影响本应用。
"""
from __future__ import annotations

import argparse
import sys
import time

JS = r"""
function findExport(modName, funcName) {
  try {
    return Process.getModuleByName(modName).getExportByName(funcName);
  } catch (e) {
    return null;
  }
}

function toHex(ptr, len) {
  const n = Math.min(len || 0, 64);
  if (n <= 0 || ptr.isNull()) return "";
  try {
    const bytes = new Uint8Array(ptr.readByteArray(n));
    return Array.from(bytes).map(b => b.toString(16).padStart(2, "0")).join(" ");
  } catch (e) {
    return "<unreadable>";
  }
}

const HID_FUNCS = ["hid_get_feature_report", "hid_send_feature_report", "hid_write", "hid_read_timeout", "hid_read"];

rpc.exports = {
  install: function () {
    const report = [];

    // 1) 厂商 SDK 里的高层函数：返回值就是电量百分比
    const sdkHooks = [
      ["HIDUsb.dll", "UsbMouseC_GetBatteryLevel"],
      ["HIDUsb.dll", "UsbMouseC_GetDeviceInfo"],
      ["HIDUsb.dll", "UsbMouseC_GetOnLine"],
      ["HIDUsb.dll", "UsbMouseDongleC_GetPairingStatus"],
    ];
    sdkHooks.forEach(function (item) {
      const addr = findExport(item[0], item[1]);
      if (!addr) return;
      report.push(item[0] + "!" + item[1]);
      Interceptor.attach(addr, {
        onEnter: function (args) { this.obj = args[0]; },
        onLeave: function (retval) {
          send({ kind: "sdk", api: item[1], value: retval.toInt32() });
        },
      });
    });

    // 2) 底层 Win32 HID 通道：HIDUsb.dll 直接用 WriteFile/ReadFile/HidD_SetFeature
    const winHooks = [
      ["kernel32.dll", "WriteFile"],
      ["kernel32.dll", "ReadFile"],
      ["hid.dll", "HidD_SetFeature"],
      ["hid.dll", "HidD_GetFeature"],
      ["hid.dll", "HidD_GetInputReport"],
    ];
    winHooks.forEach(function (item) {
      const addr = findExport(item[0], item[1]);
      if (!addr) return;
      report.push(item[0] + "!" + item[1]);
      Interceptor.attach(addr, {
        onEnter: function (args) {
          this.api = item[1];
          this.buf = args[1];
          this.len = (this.api.indexOf("HidD_") === 0) ? args[2].toInt32() : args[2].toInt32();
          this.readPtr = this.api === "ReadFile" ? args[3] : null;
        },
        onLeave: function (retval) {
          // HID 报文长度固定且很小，借长度白名单过滤掉普通文件读写
          if ([6, 7, 8, 9, 16, 20, 21, 32, 64, 65].indexOf(this.len) < 0) return;
          let got = this.len;
          if (this.readPtr && !this.readPtr.isNull()) {
            try { got = this.readPtr.readU32(); } catch (e) { got = 0; }
          }
          send({ kind: "hid", api: this.api, ret: retval.toInt32(), data: toHex(this.buf, got) });
        },
      });
    });

    // 3) hidapi 原始报文：看清命令与应答
    HID_FUNCS.forEach(function (name) {
      const addr = findExport("hidapi.dll", name);
      if (!addr) return;
      report.push("hidapi.dll!" + name);
      Interceptor.attach(addr, {
        onEnter: function (args) {
          this.name = name;
          this.buf = args[1];
          this.len = args[2].toInt32();
        },
        onLeave: function (retval) {
          send({
            kind: "hid",
            api: this.name,
            ret: retval.toInt32(),
            data: toHex(this.buf, this.len),
          });
        },
      });
    });

    return report;
  },
};
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="追踪驱动与设备的 HID 通信")
    parser.add_argument("--name", help="进程名，如 HECATE.exe")
    parser.add_argument("--pid", type=int, help="进程 ID")
    parser.add_argument("--seconds", type=float, default=12.0)
    parser.add_argument("--show-all", action="store_true", help="打印全部报文（默认只打印有变化的）")
    args = parser.parse_args()

    import frida

    target = args.pid if args.pid else args.name
    if not target:
        print("需要 --name 或 --pid")
        return 2
    try:
        session = frida.attach(target)
    except Exception as exc:  # noqa: BLE001
        print(f"附加失败：{exc}")
        return 1

    seen: dict[str, int] = {}
    interesting: list[str] = []

    def on_message(message, data):
        if message.get("type") != "send":
            print("!", message)
            return
        payload = message["payload"]
        if payload.get("kind") == "sdk":
            line = f"[SDK ] {payload['api']:34s} -> {payload['value']}"
            print(line)
            if payload["api"] == "UsbMouseC_GetBatteryLevel":
                interesting.append(f"电量读数 {payload['value']}")
            return
        data_hex = payload.get("data") or ""
        if not args.show_all:
            seen[data_hex] = seen.get(data_hex, 0) + 1
            if seen[data_hex] > 1:
                return
        print(f"[HID ] {payload['api']:24s} ret={payload['ret']:<4} {data_hex}")

    script = session.create_script(JS)
    script.on("message", on_message)
    script.load()
    hooks = script.exports_sync.install()
    print(f"已附加到 {target}，挂钩 {len(hooks)} 个函数：")
    for hook in hooks:
        print("   -", hook)
    if not hooks:
        print("没有挂到任何函数：驱动可能尚未加载 hidapi/HIDUsb，先打开设备页面再试。")

    print(f"\n观察 {args.seconds:.0f} 秒，期间请在驱动软件里打开设备/电量页面…")
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    script.unload()
    session.detach()

    if interesting:
        print("\n捕获到的电量读法：", "；".join(interesting[:5]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
