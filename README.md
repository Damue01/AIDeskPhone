# AI Desk Phone

这是一个基于 HG113 共电电话外壳改造的 AI 桌面电话项目。

当前主线不再是早期“只做 BLE 快捷键”的原型，而是以 HG113 为目标：
ESP32-C3 负责读取摘挂机开关、通过 Wi-Fi 发送轻量状态数据，并接收电脑端
命令来驱动蜂鸣器和 LED。网页、波形、动作配置、接线员模式提醒，以及后续
豆包语音能力都放在电脑端处理。

## 当前主线资料

- [HG113 产品方案](docs/HG113_PRODUCT_PLAN.md)
- [HG113 连接方式](docs/HG113_CONNECTION_MANUAL.md)
- [硬件参考资料](docs/electronics/README.md)
- [Codex 接线员 hook 配置](docs/CODEX_OPERATOR_HOOK.md)
- [2026-07-08 工作区更新汇总](docs/WORKSPACE_UPDATE_2026-07-08.md)

旧电话信号和早期 BLE HID 方案保留在 `legacy/phone-signal` 分支。

## 系统结构

```text
HG113 摘挂机开关
  -> ESP32-C3 GPIO0
  -> Wi-Fi UDP 状态上报
  -> 电脑端控制台 http://localhost:8765/

电脑端控制台 / AI hook
  -> UDP 或 USB 串口命令
  -> ESP32-C3
  -> GPIO21 蜂鸣器、GPIO20 LED

手柄音频
  -> 蓝牙耳机音频模块
  -> 电脑音频输入/输出
```

ESP32-C3 不负责承载网页。它只发送状态数据，并执行蜂鸣器、LED 等硬件命令。

## 已实现内容

- 本地网页控制台：状态显示、GPIO 波形、GPIO 配置、蜂鸣器测试、LED 测试、方案切换。
- 摘挂机判定方案可切换：
  - 方案 1：`HIGH = 按下`，`LOW = 抬起`。
  - 方案 2：`LOW = 按下`，`HIGH = 抬起`。
- Codex 或其他 AI 工具完成任务后可以调用：
  - `POST http://127.0.0.1:8765/api/ai/hook`
  - `POST http://127.0.0.1:8765/hook`
- 接线员模式提醒逻辑：任务完成后蜂鸣器和 LED 同步 `1 秒响/亮 -> 4 秒停/灭` 循环；摘机后停止。
- 约 90 秒无人接听后，普通响铃停止并切换为忙音节奏。
- 回话队列：hook 文本或手动回话会进入队列，Codex hook 可先经 Ark 角色模型润色成通讯员回报，默认复用主 API Key，摘机后播放；AI 播报中挂机会立即停止当前播报。
- 豆包 / 火山引擎语音链路：支持 TTS 2.0 流式回话播放、BigASR 流式识别、摘机录音和本地模拟页调试；用户说完后挂机会提交后台处理，完成后再电话回拨。
- 服务通信中枢地球页：`web/variant-earth-command-center/index.html` 已融合 Three.js 地球和 MapLibre 卫星地图，支持城市级缩放探索。

## 当前硬件默认引脚

```text
GPIO0  = 摘挂机开关输入
GPIO21 = 蜂鸣器输出
GPIO20 = LED 输出
```

目前已经测通的 HG113 六脚簧片开关接法：

```text
ESP32-C3 GPIO0 -> 开关 6 脚
ESP32-C3 GND   -> 开关 2 脚
```

改线前先看 [HG113 连接方式](docs/HG113_CONNECTION_MANUAL.md)。

## Wi-Fi 说明

当前稳定固件基于 pioarduino / Arduino-ESP32 3.3.9，并降低了 Wi-Fi 发射功率：

```text
WIFI_TX_POWER_QUARTER_DBM = 40
状态上报 UDP 端口       = 8766
命令接收 UDP 端口       = 8767
```

不要提交真实 Wi-Fi 密码。本地新建这个被忽略的文件即可：

```cpp
// firmware/esp32c3_gpio0_21_test/include/wifi_credentials.h
#pragma once

#define WIFI_STA_SSID "your-wifi-ssid"
#define WIFI_STA_PASSWORD "your-wifi-password"
```

## 启动控制台

```powershell
.\Start_AI_Desk_Phone.bat
```

也可以手动运行：

```powershell
.\.venv\Scripts\python.exe tools\ai_desk_phone_console.py --port COM5
```

打开：

```text
http://localhost:8765/
```

## 编译固件

```powershell
.\.venv\Scripts\platformio.exe run -d firmware\esp32c3_gpio0_21_test
```

另外两个 Wi-Fi 最小测试工程只作为排查参考保留：

```text
firmware/esp32c3_wifi_minimal_test/
firmware/esp32c3_wifi_pioarduino_test/
```

如果 Windows 终端编码导致新版 esptool 烧录输出崩掉，可以用合并镜像烧录：

```powershell
$env:PYTHONIOENCODING='utf-8'
& "$env:USERPROFILE\.platformio\penv\Scripts\esptool.exe" `
  --chip esp32c3 `
  --port COM5 `
  --baud 115200 `
  --before default-reset `
  --after hard-reset `
  write-flash -z 0x0 `
  firmware\esp32c3_gpio0_21_test\.pio\build\esp32c3_gpio0_21_test\firmware.factory.bin
```

## 验证

```powershell
.\.venv\Scripts\python.exe -m py_compile tools\ai_desk_phone_console.py tools\audio_recorder.py tools\volcengine_speech.py
.\.venv\Scripts\platformio.exe run -d firmware\esp32c3_gpio0_21_test
```

## 目录

```text
README.md
docs/HG113_PRODUCT_PLAN.md
docs/HG113_CONNECTION_MANUAL.md
docs/WORKSPACE_UPDATE_2026-07-08.md
docs/electronics/
firmware/esp32c3_gpio0_21_test/
firmware/esp32c3_wifi_minimal_test/
firmware/esp32c3_wifi_pioarduino_test/
tools/ai_desk_phone_console.py
tools/audio_recorder.py
tools/volcengine_speech.py
config/ai_desk_phone_console.json
web/variant-earth-command-center/
```

## 许可证

暂未选择开源许可证。公开复用前需要补充 `LICENSE` 文件。
