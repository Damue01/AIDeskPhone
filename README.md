# AI Desk Phone

AI Desk Phone 是一个把老式座机改造成 Windows 语音通话控制器的开源项目。

当前主线是：ESP32-C3 读取座机摘挂机/按压机构的 GPIO1/ADC 状态，并作为名为 `AIDeskPhoneKB` 的 BLE HID 键盘向 Windows 发送可配置快捷键。本地网页控制台用于调阈值、看串口日志、选择动作预设，并把配置写入 ESP32。

电话听筒的麦克风/喇叭音频链路由 CSR8645 蓝牙音频模块单独处理。音频部分与 ESP32 控制部分分开调试，接线前请先看 [制作与维护手册](docs/BUILD_MANUAL.md)。

本项目不再保留零散调试脚本。串口日志、板子状态、ADC 曲线、配置保存、模拟按下/释放都统一在网页控制台里完成。

## 当前状态

- 固件版本：`hybrid-ble-config-v1`
- 默认串口：`COM3`
- 默认控制台地址：`http://127.0.0.1:8765`
- 使用板卡：ESP32-C3 SuperMini，对应 PlatformIO 板卡配置 `esp32-c3-devkitm-1`

## 重要硬件提醒

- CSR8645 蓝牙音频模块不要默认用 ESP32-C3 的 3.3V 供电。当前现场现象显示 3.3V 可能偏低，建议优先按模块 `BAT+ / BAT-` 标称范围供电，常见是单节锂电池工作区间。除非模块明确支持 5V 输入，否则不要把 5V 直接接到 `BAT+`。
- RJ9/R9/4P4C 听筒线是四芯线，在本项目里通常拆成一对喇叭线和一对麦克风线。分不清时可以在低压音频脚上临时试接，但不要把未知听筒线接到 `BAT+`、`5V`、`3V3` 或其他电源脚。
- 当前方案不接电话外线。电话外线可能有振铃高压和未知线路状态。

## 仓库结构

```text
README.md                         项目说明和快速开始
requirements.txt                  Python 依赖
config/ai_desk_phone_console.json 控制台默认配置
firmware/esp32c3_ble_gpio/        ESP32-C3 PlatformIO 固件
tools/ai_desk_phone_console.py    本地网页控制台和串口桥
docs/BUILD_MANUAL.md              制作、接线、维护和排障手册
docs/electronics/                 硬件照片和照片索引
```

## 环境要求

- Windows 10/11
- Python 3.11 或 3.12
- ESP32-C3 开发板，通过 USB 连接电脑
- PlatformIO 和 pyserial，通过 `requirements.txt` 安装

## 安装依赖

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果本机没有 Python 3.12，可以改用 `py -3.11`。

## 编译和烧录固件

```powershell
.\.venv\Scripts\platformio.exe run -d firmware\esp32c3_ble_gpio
.\.venv\Scripts\platformio.exe run -d firmware\esp32c3_ble_gpio -t upload
```

默认上传和监视串口是 `COM3`。如果你的板子是其他串口，请修改 `firmware/esp32c3_ble_gpio/platformio.ini` 或传入 PlatformIO 参数。

## 启动控制台

```powershell
.\.venv\Scripts\python.exe tools\ai_desk_phone_console.py --port COM3 --web-port 8765
```

然后打开：

```text
http://127.0.0.1:8765
```

只预览页面、不打开串口：

```powershell
.\.venv\Scripts\python.exe tools\ai_desk_phone_console.py --no-serial --web-port 8765
```

说明：命令里的 `.venv\Scripts\python.exe` 是 Windows Python 虚拟环境的固定目录名，不是项目里额外保留的一批脚本。

## 文档

- [制作与维护手册](docs/BUILD_MANUAL.md)
- [硬件资料索引](docs/electronics/README.md)
- [照片索引](docs/electronics/photo-index.md)

## 发布前检查

```powershell
.\.venv\Scripts\python.exe -m py_compile tools\ai_desk_phone_console.py
.\.venv\Scripts\platformio.exe run -d firmware\esp32c3_ble_gpio
```

## 许可证

当前还没有选择开源许可证。正式公开前请先添加 `LICENSE` 文件。
