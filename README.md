# AI Desk Phone

AI Desk Phone 是一个把老式座机改造成 Windows 语音通话控制器的开源项目。

项目分成两条独立链路：

- 控制链路：ESP32-C3 读取座机摘挂机/按压机构，通过 BLE HID 键盘 `AIDeskPhoneKB` 向 Windows 发送快捷键。
- 音频链路：CSR8645 蓝牙音频模块连接听筒麦克风和喇叭，作为 Windows 蓝牙耳机输入/输出使用。

本项目只保留一个调试入口：本地网页控制台。串口日志、板子状态、ADC 曲线、动作日志、阈值调整和快捷键配置都在控制台里完成。

## 硬件边界

- CSR8645 蓝牙音频模块需要 3.7V 供电。不要用 ESP32-C3 的 3.3V 给它供电，也不要把 5V 直接接到 `BAT+`。
- RJ9/R9/4P4C 听筒线是四芯线，通常拆成一对喇叭线和一对麦克风线。线序以转接板标注、万用表通断和录音/播放检查为准。
- 座机本体显示异常或不亮时，优先检查座机电池；电池没电时更换电池后再继续排查。
- 当前方案不接电话外线。电话外线可能有振铃高压和未知线路状态。

## 仓库结构

```text
README.md                         项目说明和快速开始
Start_AI_Desk_Phone.bat           一键启动网页控制台
requirements.txt                  Python 依赖
config/ai_desk_phone_console.json 控制台默认配置
firmware/esp32c3_ble_gpio/        ESP32-C3 PlatformIO 固件
tools/ai_desk_phone_console.py    本地网页控制台和串口桥
docs/BUILD_MANUAL.md              制作与维护手册
docs/electronics/                 硬件照片和照片索引
```

## 快速开始

如果固件已经刷入 ESP32-C3，直接运行：

```powershell
.\Start_AI_Desk_Phone.bat
```

脚本会自动检查 Python 环境、安装依赖，并打开网页控制台。控制台会持续扫描 ESP32-C3 串口；USB 断开后重新插入，也会继续尝试连接。需要指定优先串口时：

```powershell
.\Start_AI_Desk_Phone.bat COM5
```

首次烧录固件可手动运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\platformio.exe run -d firmware\esp32c3_ble_gpio -t upload
```

打开：

```text
http://127.0.0.1:8765
```

如果手动启动控制台：

```powershell
.\.venv\Scripts\python.exe tools\ai_desk_phone_console.py --web-port 8765
```

手动指定优先串口：

```powershell
.\.venv\Scripts\python.exe tools\ai_desk_phone_console.py --port COM5 --web-port 8765
```

## 使用方式

1. 打开目标 Windows 软件，确认它用于语音输入、接听、挂断或静音的快捷键。
2. 在控制台中观察摘挂机/按压动作对应的 ADC 变化。
3. 调整阈值，让真实电话动作能稳定触发按下和释放状态。
4. 把目标软件的快捷键填入控制台的按下/释放动作。
5. 保存配置并配对 `AIDeskPhoneKB`。
6. 用电话动作验证目标软件是否按预期响应。

## 连接与保存

- 控制台只会连接 ESP32-C3 枚举出的 USB 串口，例如 `USB Serial Device (COM3)`。`COM1` 通常是 Windows 系统内置通信端口，不是本项目的板子。
- 如果控制台一直显示正在扫描，先换 USB 口或 USB 数据线，再重新插拔几次。Windows 正常识别后，控制台会自动连接新的 `COMx`。
- 保存配置时只保留一个控制台网页窗口。打开多个 `http://127.0.0.1:8765` 页面可能占用浏览器连接，导致保存请求超时。
- 保存成功需要看到日志中的 `ESP32 已确认配置写入板子。`

完整制作顺序见 [制作与维护手册](docs/BUILD_MANUAL.md)。

## 发布前检查

```powershell
.\.venv\Scripts\python.exe -m py_compile tools\ai_desk_phone_console.py
.\.venv\Scripts\platformio.exe run -d firmware\esp32c3_ble_gpio
```

## 许可证

当前还没有选择开源许可证。正式公开前请先添加 `LICENSE` 文件。
