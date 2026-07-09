# AI Desk Phone

AI Desk Phone 是一套把 HG113 共电电话外壳改造成本地 AI 桌面电话的项目。

仓库已经提供电脑端控制台、最小 Agent runtime、地球指挥中心页面、ESP32-C3 固件、语音 ASR/TTS 接入和调试工具。真实电话外壳、听筒音频、蜂鸣器、LED、供电和内部走线仍然需要你按自己的硬件实物改造；本项目不会直接把一台普通座机变成成品电话，也不接入电话外线。

## 当前能做什么

- 没有 ESP32 时，可以用本地模拟发送端和网页模拟页完整跑通摘机、挂机、回拨和 Agent 流程。
- 有 ESP32-C3 时，固件读取摘挂机开关，通过 Wi-Fi UDP 上报状态，并接收电脑端命令驱动蜂鸣器和 LED。
- 有手柄音频模块时，听筒可以作为 Windows 的麦克风和扬声器，由电脑端完成 ASR、TTS 和 Agent 处理。
- Agent 模式下，用户拿起听筒说话，ASR 实时识别；停顿或挂机后提交一轮；后台继续处理、调用工具、生成回话；任务完成后通过蜂鸣器和 LED 回拨提醒。
- Agent runtime 使用 PI 风格的会话结构：`system`、`developer`、`user`、`assistant`、`toolCall`、`toolResult`、session 文件、自动压缩和技能加载。
- 目前只加载项目本地 `.pi/skills`。默认技能是 `command-center-earth`，用于指导 Agent 操作地球/地图页面。

## 从 0 开始跑起来

先只跑电脑端，不接硬件：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\Start_AI_Desk_Phone.bat
```

启动后打开：

```text
http://127.0.0.1:8765/
http://127.0.0.1:8765/command-center/
http://127.0.0.1:8765/simulator
```

第一个页面是后台控制台，用来配置、看日志、维护 Agent session 和测试硬件动作。第二个页面是地球指挥中心。第三个页面可以模拟电话按下/抬起、模拟回拨和测试 hook。

也可以直接给 Agent 提交一轮文字：

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8765/api/agent/turn `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"source":"manual","text":"首长让地图定位到上海","reply_behavior":"direct"}'
```

## 密钥配置

复制 `.env.example` 为 `.env`，只在本机填写真实密钥，不提交 `.env`。

```text
VOLCENGINE_API_KEY=        # 豆包 / 火山引擎 ASR 和 TTS
ARK_API_KEY=               # 思考模型、通讯员回话润色、Agent 角色回复
```

这两类 key 可以不同。ASR/TTS 能用的 key，不一定能调用 Ark Chat Completions；后台页面也按这个边界分开维护。

## 两种交互模式

**输入法模式**

电话只负责触发第三方软件的语音输入、回车或快捷键。Codex 或其他 AI 完成任务后，可以调用本地 hook，把完成内容加入电话回拨队列。

**Agent 模式**

电话本身就是服务终端。摘机开始录音；停顿或挂机提交本轮语音；后台继续 ASR、工具调用和回复生成。挂机只关闭麦克风和听筒，不取消已经提交的后台任务。任务完成后，电话进入回拨；接听后播报结果。Agent 播报中挂机会暂停当前回报并重新进入回拨，用户再抬起听筒后继续听。

更细的行为契约见 [交互目标](docs/INTERACTION_TARGETS.md)。

## 项目模块

| 路径 | 作用 |
| --- | --- |
| `Start_AI_Desk_Phone.bat` | 一键启动本地控制台、模拟端和指挥中心页面。 |
| `Connect_Real_Device.bat` | 连接真实硬件时使用的辅助启动脚本。 |
| `scripts/Connect-RealDevice.ps1` | 真实 ESP32-C3 联机 SOP：检查端口、启动控制台、等待 UDP 遥测、可选测试 LED。 |
| `requirements.txt` | Python 依赖列表，启动脚本会自动安装缺失依赖。 |
| `.env.example` | 本机语音和角色模型密钥模板，复制成 `.env` 后填写，不提交真实密钥。 |
| `tools/ai_desk_phone_console.py` | 本地后端和网页控制台，端口默认 `8765`。管理配置、事件流、录音、回拨、Agent session 和硬件命令。 |
| `tools/agent_runtime.py` | 最小 PI 风格 Agent runtime，包含会话、消息、工具调用、自动压缩、本地技能加载和工具执行。 |
| `tools/volcengine_speech.py` | 豆包 / 火山引擎 ASR、TTS、Ark 角色模型调用。 |
| `tools/audio_recorder.py` | Windows 麦克风录音与音频块采集。 |
| `tools/codex_operator_hook.py` | Codex 任务完成后调用的本地 hook 客户端。 |
| `.codex/hooks/` | 仓库本地 Codex hook 配置。 |
| `.pi/skills/command-center-earth/SKILL.md` | 本项目唯一默认加载的本地技能，说明地球/地图如何被 Agent 操作。 |
| `web/variant-earth-command-center/` | 地球屏保和真实地图指挥中心页面。 |
| `firmware/esp32c3_gpio0_21_test/` | 当前主线 ESP32-C3 固件，负责 GPIO 输入、Wi-Fi 状态上报、蜂鸣器和 LED 命令。 |
| `firmware/esp32c3_wifi_minimal_test/` | Wi-Fi 排查用最小工程。 |
| `firmware/esp32c3_wifi_pioarduino_test/` | pioarduino Wi-Fi 排查工程。 |
| `docs/` | 制作、接线、交互、Hook 和硬件资料。 |
| `docs/electronics/` | 原始硬件照片、照片索引和抽象接线参考图。 |
| `tests/` | 单元测试，覆盖 Agent runtime、挂机流程、配置、硬件状态和语音服务封装。 |
| `data/agent_sessions/` | 运行时生成的 Agent session JSONL。 |
| `config/ai_desk_phone_console.json` | 本机运行配置。这里会记录你的调试状态，不建议随手提交。 |

## Agent 的基础能力

当前 Agent runtime 保持最小可用，不额外加载全局 PI skills，也不加载用户目录里的 `.agents`。它只从当前项目向上查找 `.pi/skills`。

内置工具面向这些基础能力：

- 地球/地图：返回首页、定位城市、跳转经纬度、切换阶段。
- 信息查询：天气摘要、浏览器搜索、打开 URL。
- 项目只读文件：`read`、`grep`、`find`、`ls`，只允许读项目内文件。
- 本地程序：启动 allowlist 中的应用。
- 命令执行：只执行用户明确要求的命令，带危险模式拦截、超时和输出截断。

后台页面的 Agent 维护区可以查看 system/developer prompt、当前 session、最近消息、已加载 skills、可用 tools、自动压缩摘要，也可以新建或删除当前 session。

## 硬件路线

控制链路：

```text
HG113 摘挂机开关
  -> ESP32-C3 GPIO0
  -> Wi-Fi UDP 状态上报
  -> 电脑端控制台
  -> ESP32-C3 GPIO21 蜂鸣器 / GPIO20 LED
```

音频链路：

```text
HG113 听筒麦克风和喇叭
  -> RJ9/R9/4P4C 听筒线
  -> CSR8645 或其他蓝牙音频模块
  -> Windows 麦克风 / 扬声器
```

默认引脚：

```text
GPIO0  = 摘挂机开关输入
GPIO21 = 蜂鸣器输出
GPIO20 = LED 输出
```

已经测通的一种 HG113 六脚簧片开关接法：

```text
ESP32-C3 GPIO0 -> 开关 6 脚
ESP32-C3 GND   -> 开关 2 脚
```

不同电话批次、线序和改造方式可能不同。改线前先看 [HG113 连接方式](docs/HG113_CONNECTION_MANUAL.md) 和 [制作与维护手册](docs/BUILD_MANUAL.md)，用万用表确认，不要凭颜色直接接线。电话外线不要接入。

## ESP32-C3 固件

本地创建 Wi-Fi 凭据文件，不要提交真实 SSID 和密码：

```cpp
// firmware/esp32c3_gpio0_21_test/include/wifi_credentials.h
#pragma once

#define WIFI_STA_SSID "your-wifi-ssid"
#define WIFI_STA_PASSWORD "your-wifi-password"
```

编译：

```powershell
.\.venv\Scripts\platformio.exe run -d firmware\esp32c3_gpio0_21_test
```

烧录：

```powershell
.\.venv\Scripts\platformio.exe run -d firmware\esp32c3_gpio0_21_test -t upload
```

也可以在控制台“调试与校准 -> 维护”里使用固件烧录按钮。默认运行使用 Wi-Fi UDP / 模拟链路；需要串口调试时，在后台页面打开“串口调试”。

## 常用接口

```text
GET  http://127.0.0.1:8765/events
GET  http://127.0.0.1:8765/api/agent/status
POST http://127.0.0.1:8765/api/agent/start
POST http://127.0.0.1:8765/api/agent/turn
POST http://127.0.0.1:8765/api/agent/session/new
POST http://127.0.0.1:8765/api/agent/session/delete
POST http://127.0.0.1:8765/api/ai/hook
POST http://127.0.0.1:8765/api/simulate/release
POST http://127.0.0.1:8765/api/simulate/press
POST http://127.0.0.1:8765/api/hardware/beep
```

地球页面桥接 API 见 [控制指挥中心页面](web/variant-earth-command-center/README.md) 和 [API 参考](web/variant-earth-command-center/API.md)。

## 推荐建设顺序

1. 先不接硬件，启动控制台、模拟页和地球页。
2. 用 `/api/agent/turn` 跑通定位城市、回到地球首页、天气/搜索等基础工具。
3. 配好 `VOLCENGINE_API_KEY` 和 `ARK_API_KEY`，测试 ASR、TTS 和通讯员回话。
4. 烧录 ESP32-C3，接 GPIO0 摘挂机输入，确认后台波形和按下/抬起状态。
5. 接蜂鸣器和 LED，确认回拨提醒节奏。
6. 接 CSR8645 或其他音频模块，把听筒接成 Windows 麦克风和扬声器。
7. 根据你的地球页面继续扩展 `.pi/skills`，让 Agent 学会新的本地能力。

真实设备联机时也可以直接用：

```powershell
.\Connect_Real_Device.bat
```

这个脚本会在 `8768` 端口启动控制台、关闭模拟模式、等待 ESP32-C3 的 UDP 遥测，并把排查日志写到 `logs/`。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover tests -v
.\.venv\Scripts\platformio.exe run -d firmware\esp32c3_gpio0_21_test
```

如果只是改文档，不一定需要重新跑固件编译；涉及控制台、Agent runtime 或固件时建议都跑。

## 继续阅读

- [制作与维护手册](docs/BUILD_MANUAL.md)
- [交互目标](docs/INTERACTION_TARGETS.md)
- [Codex 接线员 hook 配置](docs/CODEX_OPERATOR_HOOK.md)
- [HG113 产品方案](docs/HG113_PRODUCT_PLAN.md)
- [HG113 连接方式](docs/HG113_CONNECTION_MANUAL.md)
- [硬件参考资料](docs/electronics/README.md)
- [地球指挥中心页面](web/variant-earth-command-center/README.md)

## 许可证

暂未选择开源许可证。公开复用前需要补充 `LICENSE` 文件。
