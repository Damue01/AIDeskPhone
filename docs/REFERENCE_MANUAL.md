# AI Desk Phone 标准参考手册

本文是 AI Desk Phone 当前实现的权威操作手册，适用于使用者、硬件制作者和项目维护者。HTTP 与协议细节见 [API 参考](API_REFERENCE.md)，样机照片见[硬件资料索引](electronics/README.md)。

## 1. 项目范围与状态

AI Desk Phone 把 HG113 桌面电话外壳改造成 Windows 主机的实体 AI 终端。当前功能包括：

- 摘挂机状态采集、LED 和蜂鸣器控制；
- Windows 快捷键映射；
- 录音、流式语音识别、Agent 工具调用和语音播报；
- Codex 或其他本地工具的任务完成回拨；
- 本地配置控制台、模拟器和地球指挥中心；
- ESP32-S3 Wi-Fi 配网、UDP 遥测和 TCP/UDP 命令链路。

项目状态为**实验性原型**。它不是电话交换设备，不接入公共电话网，也没有消费电子产品所需的电气、无线电或安全认证。

### 1.1 当前支持矩阵

| 组件 | 状态 | 说明 |
| --- | --- | --- |
| Windows 10/11 | 当前支持 | 快捷键、音频、BAT/PowerShell 脚本为 Windows 优先 |
| Python 3.11、3.12 | 当前支持 | 启动脚本自动尝试可用版本 |
| ESP32-S3 DevKitC-1 | 当前硬件基线 | 固件环境 `esp32s3_gpio0_21_test` |
| ESP32-C3 | 历史兼容/诊断 | 不作为当前推荐成品主控 |
| CSR8645 蓝牙音频模块 | 参考方案 | 不属于 Python 或固件的硬依赖 |
| 无硬件模拟器 | 当前支持 | 一键模拟模式隔离真实硬件 I/O 和 Windows 快捷键 |
| macOS、Linux | 未支持 | 部分 Python 能力可能运行，但未形成完整验证路径 |

### 1.2 命名说明

主固件目录仍名为 `firmware/esp32c3_gpio0_21_test/`，这是历史路径。当前构建必须显式选择其中的 ESP32-S3 环境：

```text
esp32s3_gpio0_21_test
```

`AiLandLine` 是早期兼容标识，仍出现在配网 SSID、固件 namespace 和 JavaScript 别名中；产品名称统一使用 **AI Desk Phone**。

## 2. 安全边界

### 2.1 电气安全

1. 不连接电话外线、公共交换电话网或任何未知线路。
2. ESP32-S3 只连接经过确认的低压干接点、LED、蜂鸣器和公共地。
3. CSR8645 参考模块使用 3.7 V 供电；不要把 5 V 直接接到 `BAT+`，也不要用 ESP32 的 3.3 V 引脚为其供电。
4. 裸 LED 必须串联限流电阻。
5. 原电话铃器、线圈或大功率蜂鸣器通常不能由 GPIO 直接驱动，应使用合适的三极管、MOSFET 或驱动模块。
6. 听筒喇叭常为差分输出，不要把 `L-` 或 `R-` 直接当作 GND。
7. 装机前用万用表确认触点、极性、供电和短路；所有焊点必须绝缘并做应力释放。

### 2.2 网络与命令安全

- HTTP 控制台默认只绑定 `127.0.0.1`，且没有登录或令牌认证。
- UDP 8766、UDP 8767 和 TCP 8768 用于 ESP32 局域网通信，没有加密、签名或设备认证。
- 不要做公网端口转发，不要把服务部署到不受信任的 Wi-Fi 或访客网络。
- 默认 `confirm_sensitive` 权限禁止 Agent 运行 shell。`commander`、`developer` 和 `trusted` 会开放显式命令工具，只应在可信主机上使用。
- 详细风险和防护见 [SECURITY.md](../SECURITY.md) 与 [THREAT_MODEL.md](../THREAT_MODEL.md)。

## 3. 系统架构

### 3.1 硬件与桌面端职责

```text
HG113 摘挂机干接点
  -> ESP32-S3 GPIO4
  -> UDP 8766 广播遥测
  -> Windows Python 控制台

Windows Python 控制台
  -> TCP 8768 / UDP 8767 / 可选串口
  -> ESP32-S3 GPIO2 蜂鸣器、GPIO1 LED

HG113 听筒
  -> 蓝牙音频模块
  -> Windows 麦克风和扬声器
  -> ASR / Agent / TTS
```

ESP32-S3 不处理麦克风或扬声器音频。固件中的 HTTP 80 只用于临时 Wi-Fi 配网，不承载桌面控制台。

### 3.2 桌面端进程

`tools/ai_desk_phone_console.py` 是单进程、多线程服务：

```text
HTTP / REST / SSE
├─ AppState：配置、状态、日志、回拨和 Agent 会话
├─ UDP 线程：接收 ESP32 遥测
├─ TCP 线程：向 ESP32 推送命令
├─ 可选串口线程：调试和兜底
├─ 录音与流式 ASR
├─ Agent runtime 与项目内 skills
├─ TTS 与音频播放
└─ 指挥中心静态资源
```

### 3.3 状态约定

当前固件使用 `INPUT_PULLUP`：

| 实际动作 | GPIO4 | 固件状态 | 桌面状态 |
| --- | --- | --- | --- |
| 听筒放下、挂机 | `HIGH` | `ON_HOOK` | `PRESSED` |
| 听筒拿起、摘机 | `LOW` | `OFF_HOOK` | `RELEASED` |

`PRESSED`/`RELEASED` 是历史协议名称，阅读配置时不要按自然语言反向理解。

## 4. 软件安装

### 4.1 前置条件

- Windows 10 或 Windows 11；
- Python 3.11 或 3.12；
- Git；
- 烧录固件时需要支持数据传输的 USB 线；
- 真实设备需要 2.4 GHz Wi-Fi，电脑与 ESP32 必须位于可达的可信局域网。

### 4.2 克隆和安装

```powershell
git clone https://github.com/Damue01/AIDeskPhone.git
Set-Location AIDeskPhone
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

安装成功后，下列命令应返回 PlatformIO 版本和设备列表：

```powershell
.\.venv\Scripts\platformio.exe --version
.\.venv\Scripts\platformio.exe device list
```

如果没有 `py -3.12`，使用 `py -3.11` 或已安装的 `python` 创建虚拟环境。

## 5. 无硬件模拟

### 5.1 启动

```powershell
.\Start_AI_Desk_Phone.bat simulator
```

指定其他 Web 端口：

```powershell
.\Start_AI_Desk_Phone.bat simulator 8770
```

启动脚本会创建虚拟环境、安装缺失依赖，并打开 `/simulator`。它同时传入 `--simulation-only`、`--no-serial` 和 `--no-actions`，因此不会扫描串口、监听 ESP32 UDP/TCP 设备链路、发送硬件命令、烧录固件或发送 Windows 快捷键。

### 5.2 成功判据

1. `http://127.0.0.1:8765/simulator` 可以打开。
2. 模拟摘机/挂机后，状态和日志发生变化。
3. `/api/hardware/status` 返回 JSON。
4. 手动提交带文本的 Hook 后，回拨队列出现一条记录。

体验页面和状态机不需要 API Key；ASR、云端 TTS 和 Ark 回复需要对应密钥。

## 6. 硬件制作

### 6.1 参考材料

可采购规格、数量和替代件要求见 [ESP32-S3 参考物料清单](electronics/BOM.md)。下表只概括系统组成：

| 材料 | 用途 | 注意事项 |
| --- | --- | --- |
| HG113 电话外壳与听筒 | 机械结构和音频终端 | 电话外线必须断开 |
| ESP32-S3 DevKitC-1 | GPIO、Wi-Fi、LED、蜂鸣器 | 当前固件基线 |
| 蜂鸣器或驱动模块 | 回拨提示音 | 大电流负载不可直连 GPIO |
| LED 与限流电阻 | 状态提示 | 当前信号引脚 GPIO1 |
| CSR8645 或同类蓝牙音频模块 | 听筒音频桥接 | 参考方案，3.7 V 供电 |
| RJ9/R9/4P4C 转接线 | 分离麦克风和喇叭线 | 线序必须实测 |
| 万用表、热缩管、绝缘材料 | 检测和装配 | 装机前必须检查短路 |

### 6.2 当前接线

![HG113 与 ESP32-S3 参考接线图](electronics/assets/hg113_reference_wiring.svg)

| 功能 | ESP32-S3 | HG113/外设 | 说明 |
| --- | --- | --- | --- |
| 摘挂机输入 | GPIO4 | 六脚叉簧/簧片开关 6 号脚 | 内部上拉 |
| 摘挂机地线 | GND | 开关 2 号脚 | 2–6 作为一对干接点 |
| 蜂鸣器 | GPIO2 | 信号端或驱动输入 | 3 kHz PWM |
| LED | GPIO1 | LED 信号端 | 裸 LED 需限流电阻 |
| 公共地 | GND | 蜂鸣器/LED 地 | 必须共地 |

HG113 六脚开关参考编号：

```text
第一排：1 2 3
第二排：4 5 6
```

2 与 6 是同一对机械干接点，本身没有正负。不要把电话原板供电、3.3 V、电话线或其他电压接入这对触点。

### 6.3 音频链路

参考接法：

```text
听筒麦克风 -> CSR8645 MIC+ / MIC-
听筒喇叭   -> CSR8645 L+ / L- 或 R+ / R-
CSR8645    -> Windows 蓝牙麦克风和扬声器
```

操作顺序：

1. 按模块要求接入 3.7 V 电源。
2. 让模块进入配对模式，并在 Windows 中确认输入、输出设备均可用。
3. 用万用表和录音/播放测试确认听筒四芯线的两对功能。
4. 完成录音和播放验证后再焊接、固定和绝缘。

线色只能作为线索，不能作为接线依据。

## 7. 固件

### 7.1 当前工程

```text
目录：firmware/esp32c3_gpio0_21_test/
环境：esp32s3_gpio0_21_test
板型：esp32-s3-devkitc-1
框架：Arduino
串口：115200 baud
```

其他目录用途：

| 目录 | 状态 | 用途 |
| --- | --- | --- |
| `esp32c3_wifi_pioarduino_test` | 诊断 | C3/S3 Wi-Fi 扫描和连接验证 |
| `esp32c3_wifi_minimal_test` | 历史诊断 | C3 最小 Wi-Fi 工程 |
| `esp32c3_ble_gpio` | 历史 | 旧 BLE HID/ADC 路线，不是当前主链路 |

### 7.2 编译

```powershell
.\.venv\Scripts\platformio.exe run `
  -d firmware\esp32c3_gpio0_21_test `
  -e esp32s3_gpio0_21_test
```

成功判据是命令以 `SUCCESS` 结束，并生成 ESP32-S3 环境的构建产物。

### 7.3 烧录

先查询当前串口：

```powershell
.\.venv\Scripts\platformio.exe device list
```

再显式指定环境和端口：

```powershell
.\.venv\Scripts\platformio.exe run `
  -d firmware\esp32c3_gpio0_21_test `
  -e esp32s3_gpio0_21_test `
  -t upload `
  --upload-port COM7
```

复位或重新插拔后 COM 号可能变化。不要直接照抄样机端口；每次失败后重新运行 `device list`。

### 7.4 Wi-Fi 配网

没有保存凭据，或连续三次连接失败后，固件会启动临时配网热点：

```text
SSID：AiLandLine-Setup
密码：ailandline
页面：http://192.168.4.1/
```

步骤：

1. 给 ESP32-S3 上电。
2. 在电脑或手机上连接 `AiLandLine-Setup`。
3. 若 captive portal 没有自动弹出，打开 `http://192.168.4.1/`。
4. 输入 2.4 GHz Wi-Fi 的 SSID 和密码。
5. `Computer command host` 在 Windows 移动热点下可留空或填 `auto`。
6. 普通路由器下，为获得稳定 TCP 命令链路，建议填写运行控制台的电脑 IPv4 地址。
7. 保存并等待设备重连。

凭据保存在 ESP32 NVS 的 `ailandline` namespace。配网页面使用固定热点密码和明文 HTTP，只能在现场、可信环境中短时使用。

开发者也可以创建被 Git 忽略的文件：

```text
firmware/esp32c3_gpio0_21_test/include/wifi_credentials.h
```

```cpp
#pragma once

#define WIFI_STA_SSID "YOUR_WIFI_SSID"
#define WIFI_STA_PASSWORD "YOUR_WIFI_PASSWORD"
#define COMMAND_SERVER_HOST_TEXT "192.168.1.23"
```

不要提交真实 Wi-Fi 凭据。

### 7.5 固件命令

固件接受串口、UDP 8767 或 TCP 8768 的单行命令：

| 命令 | 作用 |
| --- | --- |
| `ping` | 请求设备状态 |
| `config` | 应用引脚、采样间隔和去抖配置 |
| `set_pins` | 更新运行时引脚 |
| `beep`、`ring`、`ring_once` | 响约 600 ms |
| `ring_on`、`buzzer_on` | 持续打开蜂鸣器 |
| `ring_off`、`buzzer_off` | 关闭蜂鸣器 |
| `led_on`、`led_off` | 单独控制 LED |
| `provision`、`wifi_setup`、`setup_portal` | 重新进入配网模式 |

控制台目前没有单独的通用配网 REST 路由；需要重新配网时，可通过串口或原始设备命令发送上述命令。

## 8. 连接真实设备

### 8.1 推荐启动

```powershell
.\Connect_Real_Device.bat
```

脚本默认使用：

```text
HTTP：TCP 8765
遥测：UDP 8766
设备命令：UDP 8767
持久命令：TCP 8768
```

指定串口：

```powershell
.\Connect_Real_Device.bat -SerialPort COM7
```

同时测试 LED：

```powershell
.\Connect_Real_Device.bat -SerialPort COM7 -TestLed
```

脚本只会停止占用当前 Web 端口、且命令行明确匹配 `tools/ai_desk_phone_console.py` 的旧控制台；若端口属于其他程序，它会中止并要求人工处理。随后脚本检查全部端口、启动无模拟服务、等待真实 UDP 遥测，并把输出写入 `logs/`。

### 8.2 常规启动脚本

```powershell
.\Start_AI_Desk_Phone.bat
.\Start_AI_Desk_Phone.bat COM7
.\Start_AI_Desk_Phone.bat COM7 8770
```

无参数时使用 Wi-Fi 主链路；是否启用串口由保存的控制台配置决定。传入 `COMx` 会强制启用串口调试。

不自动打开浏览器：

```powershell
$env:AI_DESK_PHONE_NO_BROWSER='1'
.\Start_AI_Desk_Phone.bat
```

### 8.3 成功判据

打开 `http://127.0.0.1:8765/`，确认：

1. `real_device_connected = true`；
2. 状态显示 `hook_pin = 4`、`buzzer_pin = 2`、`led_pin = 1`；
3. 设备具有有效 Wi-Fi IP 和 RSSI；
4. 摘机时为 `LOW / OFF_HOOK / RELEASED`；
5. 挂机时为 `HIGH / ON_HOOK / PRESSED`；
6. `LED on/off` 能控制 GPIO1；
7. `beep` 或 `ring_on/off` 能控制 GPIO2；
8. 设备状态在命令后及时回报确认。

快速状态查询：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/hardware/status
```

## 9. 使用模式

### 9.1 输入法模式

输入法模式把电话动作映射为 Windows 快捷键：

```text
摘机 -> 当前方案的 RELEASED 动作
挂机 -> 当前方案的 PRESSED 动作
外部任务完成 -> Hook -> 电话回拨
```

在控制台中先选择或创建输入方案，再用模拟按钮验证快捷键。更换目标应用时只需修改动作方案，不需要重新烧录固件。

### 9.2 Agent 模式

```text
摘机 -> 16 kHz 单声道录音 -> 流式 ASR
     -> Agent 规划与工具调用 -> 角色回复
     -> TTS 播报或挂机回拨
```

当前自动提交条件：

- 最短录音 1 秒；
- 静音约 1.1 秒；
- ASR 文本稳定约 1.6 秒；
- 单轮最长约 25 秒。

挂机不会取消已经提交的后台任务。播报中挂机会停止当前播放并把回复重新放回回拨流程。

项目内 skills 只从 `.pi/skills/` 加载。默认地球 skill 位于：

```text
.pi/skills/command-center-earth/SKILL.md
```

### 9.3 回拨与播报

```text
任务完成 -> 回复入队 -> LED/蜂鸣器提醒 -> 摘机 -> TTS 播报
```

默认节奏：

- 1 秒响/亮，4 秒停/灭；
- 90 秒未接听后切换为 0.5 秒忙音节奏；
- 120 秒未接听后，本次普通回拨过期；
- 同一来源 20 秒内的重复完成通知会去重。

关闭 `enable_callback` 后，挂机状态下的完成回复不会进入回拨队列。

## 10. 语音和模型配置

### 10.1 最小密钥

```powershell
Copy-Item .env.example .env
```

```dotenv
VOLCENGINE_API_KEY=YOUR_SPEECH_API_KEY
ARK_API_KEY=YOUR_ARK_API_KEY
```

| 变量 | 用途 |
| --- | --- |
| `VOLCENGINE_API_KEY` | BigASR、豆包 TTS |
| `ARK_API_KEY` | Ark Chat、通讯员润色、Agent 回复和工具调用 |

语音 API Key 不一定有 Ark Chat 权限；遇到 401/403 时分别检查两套凭据。

### 10.2 TTS

常用变量：

```text
DOUBAO_TTS_ENABLED
DOUBAO_TTS_ENDPOINT
DOUBAO_TTS_RESOURCE_ID
DOUBAO_TTS_MODEL
DOUBAO_TTS_SPEAKER
DOUBAO_TTS_FORMAT
DOUBAO_TTS_SAMPLE_RATE
DOUBAO_TTS_STREAMING_PLAYBACK_ENABLED
DOUBAO_TTS_EXPLICIT_LANGUAGE
DOUBAO_TTS_EXPLICIT_DIALECT
DOUBAO_TTS_DISABLE_MARKDOWN_FILTER
DOUBAO_TTS_DISABLE_EMOJI_FILTER
```

播放顺序是：豆包流式 PCM、豆包完整 WAV、Windows System.Speech；都不可用时只模拟播放时长并记录错误。

### 10.3 ASR

```text
DOUBAO_ASR_ENABLED
DOUBAO_ASR_ENDPOINT
DOUBAO_ASR_RESOURCE_ID
DOUBAO_ASR_MODEL
DOUBAO_ASR_CHUNK_MS
DOUBAO_ASR_STREAMING_ENABLED
DOUBAO_ASR_BOOSTING_TABLE_ID
DOUBAO_ASR_BOOSTING_TABLE_NAME
DOUBAO_ASR_HOTWORDS
```

`DOUBAO_ASR_CHUNK_MS` 在代码中限制为 20–500 ms。项目名、HG113、Codex、地名和自定义词可加入热词。

### 10.4 Ark 角色模型

```text
ARK_CHAT_COMPLETIONS_ENDPOINT
DOUBAO_OPERATOR_POLISH_ENABLED
DOUBAO_OPERATOR_MODEL
DOUBAO_OPERATOR_SYSTEM_PROMPT
DOUBAO_OPERATOR_MAX_TOKENS
DOUBAO_OPERATOR_USE_SPEECH_API_KEY
```

默认 persona 名称为“小叶”，默认称呼为“首长”；这是可配置的角色策略，不是协议要求。

### 10.5 旧凭据兼容

代码仍接受 `VOLCENGINE_APP_KEY`、`VOLCENGINE_APP_ID`、`VOLCENGINE_ACCESS_KEY`、`VOLCENGINE_ACCESS_TOKEN` 以及部分 `DOUBAO_*` 别名。新部署优先使用 `.env.example` 中的最小 API Key 配置。

## 11. Codex 完成通知

AI Desk Phone 使用 Codex 的用户级 `notify` 配置接收 `agent-turn-complete` 通知。OpenAI 官方说明 `notify` 会执行外部命令并传入一段 JSON；同时，项目级 `.codex/config.toml` 不能覆盖 `notify`，因此配置必须位于用户级 `~/.codex/config.toml`。参见 [Codex Notifications](https://developers.openai.com/codex/config-advanced#notifications)。

### 11.1 前置条件

1. AI Desk Phone 控制台正在运行。
2. 控制台中启用了“完成后电话回拨”。
3. 本仓库虚拟环境可用。
4. 使用绝对路径配置 notify。

### 11.2 用户级配置示例

在 `%USERPROFILE%\.codex\config.toml` 中设置，并把路径替换为实际仓库位置：

```toml
notify = [
  "E:/path/to/AIDeskPhone/.venv/Scripts/python.exe",
  "-X",
  "utf8",
  "E:/path/to/AIDeskPhone/.codex/hooks/ai_desk_phone_notify.py"
]
```

包装器会先尝试执行原有桌面通知命令，再调用 `tools/codex_operator_hook.py`。任一步失败都会被记录并跳过，不阻断 Codex 正常结束。

如果原有上游通知命令不在 `PATH`，可以设置：

```powershell
$env:CODEX_AI_DESK_PHONE_UPSTREAM_NOTIFY='C:\path\to\notifier.exe turn-ended'
```

当前包装器对所有工作目录都会调用电话 Hook；工作目录只用于选择匹配的 Codex 会话回退文本。不要继续使用已删除的 `.codex/hooks.json` 或 `ai_desk_phone_stop_hook.py`。

### 11.3 手动验证

先直接验证 HTTP Hook：

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8765/api/ai/hook `
  -Method Post `
  -ContentType 'application/json' `
  -Body '{"source":"manual","text":"标准手册整理已经完成。"}'
```

可靠测试必须提供 `text`。只有 `source` 而没有完成文本时，控制台可能丢弃请求。

Hook 客户端变量：

```text
AI_DESK_PHONE_HOOK_URL
AI_DESK_PHONE_SOURCE
AI_DESK_PHONE_DEFAULT_HOOK_TEXT
AI_DESK_PHONE_SKIP_CALLBACK_CHECK
AI_DESK_PHONE_DISABLE_SESSION_FALLBACK
AI_DESK_PHONE_SESSION_LOOKBACK_SECONDS
AI_DESK_PHONE_NOTIFY_CWD
CODEX_HOME
CODEX_AI_DESK_PHONE_UPSTREAM_NOTIFY
```

## 12. 控制台配置参考

配置文件：

```text
config/ai_desk_phone_console.json
```

网页保存配置会改写该文件。开发仓库中出现配置差异时，先确认是否是有意的默认值修改，再决定是否提交。

### 12.1 模式和状态

| 字段 | 说明 |
| --- | --- |
| `business_mode` | `codex` 为输入法/外部任务模式，`doubao` 为 Agent 模式 |
| `hook_scheme` | `scheme1` 或 `scheme2`，用于切换状态解释方向 |
| `enable_actions` | 是否发送 Windows 快捷键 |
| `enable_callback` | 是否在挂机时保留完成回拨 |
| `enable_serial_debug` | 是否启动串口扫描/调试 |

### 12.2 硬件和采样

| 字段 | 当前默认值 | 说明 |
| --- | ---: | --- |
| `hook_pin` | 4 | 摘挂机输入 |
| `buzzer_pin` | 2 | 蜂鸣器 PWM 输出 |
| `led_pin` | 1 | LED 输出 |
| `udp_device_host` | 空 | 最近发现设备地址，可自动更新 |
| `udp_command_port` | 8767 | ESP32 UDP 命令端口 |
| `sample_interval_ms` | 250 | 设备采样周期 |
| `debounce_ms` | 120 | 桌面状态机去抖参数 |

`press_threshold`、`release_threshold`、评分步长和峰值保持等字段来自旧 ADC 路线。当前主固件上报合成 ADC 0/4095，核心状态以数字电平为准。

### 12.3 输入动作

| 字段 | 说明 |
| --- | --- |
| `input_action_profiles` | 输入方案数组 |
| `active_input_profile_id` | 当前方案 ID |
| `press_action_text` | 挂机/PRESSED 动作的兼容字段 |
| `release_action_text` | 摘机/RELEASED 动作的兼容字段 |

动作文本支持组合键、按键和延迟。先在模拟模式验证，再启用真实动作。

### 12.4 语音和 Agent

| 字段 | 说明 |
| --- | --- |
| `enable_tts_playback` | 是否播放 TTS |
| `audio_output_device` | 输出设备名称或默认设备 |
| `enable_voice_asr` | 是否启用语音识别 |
| `voice_record_sample_rate` | 录音采样率，当前默认 16000 |
| `voice_record_device` | 输入设备名称或默认设备 |
| `voice_auto_transcribe` | 是否自动转写 |
| `voice_reply_policy` | `direct`、`callback` 或 `silent` |
| `agent_permission_profile` | Agent 命令权限档位 |

权限档位：

| 值 | shell 权限 |
| --- | --- |
| `confirm_sensitive` | 禁止；当前不会弹出二次确认框 |
| `commander` | 允许 |
| `developer` | 允许 |
| `trusted` | 允许 |

命令工具只有危险模式黑名单、15 秒超时和输出截断，不等同于完整沙箱。

## 13. 页面、端口与数据

### 13.1 页面

| 地址 | 用途 |
| --- | --- |
| `http://127.0.0.1:8765/` | 配置与调试控制台 |
| `http://127.0.0.1:8765/command-center/` | 地球指挥中心 |
| `http://127.0.0.1:8765/simulator` | 无硬件模拟台 |
| `http://127.0.0.1:8765/events` | Server-Sent Events |

完整接口见 [API_REFERENCE.md](API_REFERENCE.md)。

### 13.2 端口

| 端口 | 协议 | 方向 | 用途 |
| --- | --- | --- | --- |
| 8765 | TCP | 浏览器 → PC | HTTP、REST、SSE |
| 8766 | UDP | ESP32 → PC | 广播遥测 |
| 8767 | UDP | PC → ESP32 | 硬件命令 |
| 8768 | TCP | ESP32 → PC | 持久命令通道 |
| 80 | TCP | 浏览器 → ESP32 | 临时配网页面 |
| 53 | UDP | 客户端 → ESP32 | captive DNS |
| 115200 | 串口 | PC ↔ ESP32 | USB 调试和命令 |

设备状态超过约 8 秒未更新会被标记为不新鲜。硬件命令默认等待约 2.5 秒的状态确认。

### 13.3 本地数据

| 路径 | 内容 | Git 状态 |
| --- | --- | --- |
| `.env` | API Key 和服务配置 | 忽略 |
| `data/recordings/` | 用户录音 WAV | 忽略 |
| `data/tts/` | 合成语音 WAV | 忽略 |
| `data/agent_sessions/` | Agent JSONL 会话 | 忽略 |
| `logs/` | 实机脚本输出和错误 | 忽略 |
| `config/ai_desk_phone_console.json` | 控制台配置 | 跟踪 |

删除 `data/` 会清除本地录音和 Agent 会话；删除前先停止控制台并确认没有需要保留的数据。

项目不会自动清理这些文件。“删除 Agent 会话”只删除当前会话文件，不会清理录音、TTS、其他历史会话、日志或 ESP32 NVS 中的 Wi-Fi 凭据。完整数据流、默认保留行为和设备擦除方法见 [隐私与本地数据](../PRIVACY.md)。

## 14. 故障排查

### 14.1 页面打不开

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen
```

若端口被旧进程占用，重新运行 `Start_AI_Desk_Phone.bat` 或 `Connect_Real_Device.bat`。启动脚本会尝试停止旧控制台。

### 14.2 只有 COM1

COM1 通常是 Windows 系统串口，不是 ESP32。检查 USB 数据线、USB 口、驱动、BOOT/RESET 状态，然后重新运行：

```powershell
.\.venv\Scripts\platformio.exe device list
```

### 14.3 停在 ESP-ROM 或无法上传

- 确认目标是 ESP32-S3 环境；
- 重新查询 COM 号；
- 检查 BOOT 键是否被持续按下；
- 重新插拔或复位设备；
- 关闭占用串口的监视器和旧控制台。

### 14.4 没有 UDP 遥测

1. 确认 PC 和 ESP32 位于同一可达 LAN/VLAN。
2. 临时关闭会阻断广播的 VPN。
3. Windows 防火墙允许 Python 入站 UDP 8766。
4. 给 ESP32 重新上电，观察串口 Wi-Fi 日志。
5. 检查 SSID、密码和 2.4 GHz 网络。

### 14.5 TCP 命令不通

Windows 移动热点下 `Computer command host = auto` 通常可用。普通路由器的 gateway 通常不是电脑，应在配网页面填写电脑固定 IPv4，并允许入站 TCP 8768。

### 14.6 摘挂机方向反了

先切换 `hook_scheme`。确认 GPIO4 与 GND 只连接机械干接点，不要混入原电话板供电。

### 14.7 LED 亮但蜂鸣器不响

确认当前接线是 GPIO1 LED、GPIO2 蜂鸣器。如果状态显示 `buzzer = ON`，说明命令已经到达设备；继续检查蜂鸣器类型、极性、供电和驱动能力。

### 14.8 ASR/TTS 不可用

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/speech/status
```

分别检查 speech key 与 Ark key、输入输出设备、服务 endpoint 和资源 ID。日志中不得公开粘贴完整密钥。

### 14.9 Codex 完成后不回拨

1. 确认控制台运行且 `enable_callback = true`。
2. 手动 POST 一条带 `text` 的 Hook。
3. 确认用户级 `notify` 使用绝对路径。
4. 检查 Hook 是否在 20 秒去重窗口内重复触发。
5. 保持电话挂机，才能测试完整响铃流程。

### 14.10 日志乱码

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
```

固件烧录后台已强制 UTF-8；其他临时 PowerShell 会话可显式设置上述变量。

## 15. 开发和维护

### 15.1 仓库结构

```text
.codex/hooks/   Codex notify 包装器
.pi/skills/     项目内 Agent skills
config/         控制台默认配置
docs/           权威手册、API 和硬件资料
firmware/       ESP32 主固件与诊断工程
scripts/        Windows 实机 SOP
tests/          单元和事实一致性测试
tools/          Python 控制台、Agent、语音与 Hook
web/            地球指挥中心
```

### 15.2 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover tests -v
```

该命令覆盖状态机、Agent、硬件协议、启动脚本、Hook、语音 payload 和文档链接。固件另行构建：

```powershell
.\.venv\Scripts\platformio.exe run `
  -d firmware\esp32c3_gpio0_21_test `
  -e esp32s3_gpio0_21_test
```

### 15.3 文档规则

- 引脚、端口、环境变量和命令只在本手册或 API 参考中维护一次。
- 历史试验不放回用户手册，改用 Issue 或 Git 历史追溯。
- 行为变更必须同步测试和文档。
- 硬件 PR 必须写明板型、引脚、固件环境、是否实机验证和安全影响。
- 完整流程见 [CONTRIBUTING.md](../CONTRIBUTING.md)。

## 16. 术语表

| 术语 | 含义 |
| --- | --- |
| 摘机 | 拿起听筒；协议状态 `LOW / OFF_HOOK / RELEASED` |
| 挂机 | 放下听筒；协议状态 `HIGH / ON_HOOK / PRESSED` |
| 回拨 | 任务完成后通过 LED/蜂鸣器提醒用户接听 |
| 播报 | 用户接听后播放 TTS 回复 |
| 控制台 | `tools/ai_desk_phone_console.py` 提供的本地服务和配置页 |
| 指挥中心 | 地球/地图状态页面 |
| Agent 模式 | 电话直接完成 ASR、工具调用和回复的模式 |
| 输入法模式 | 电话向第三方桌面应用发送快捷键并接收完成 Hook 的模式 |
| `AiLandLine` | 旧兼容 namespace，不是当前产品名 |
