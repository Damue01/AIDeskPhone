# Codex 接线员 Hook

这个 hook 用来把 Codex 或其他本地 AI 工具的“任务完成”消息送进 AI Desk Phone。控制台收到消息后，会把文本整理成通讯员回话、加入回拨队列，并通过蜂鸣器和 LED 提醒用户接听。

ESP32-C3 不承载网页，也不处理 AI 文本。它只负责 GPIO 状态上报和执行蜂鸣器、LED 等硬件命令。

## 什么时候用

- Codex 在电脑上完成了一轮任务，需要电话提醒你回来听结果。
- 其他脚本或工具完成任务后，希望复用同一套电话回拨队列。
- 没有真实硬件时，也可以只用模拟页验证流程。

如果你正在使用 Agent 模式，电话语音输入、ASR、工具调用和回复生成由本地控制台处理；hook 主要用于第三方任务完成提醒。

## 启动控制台

```powershell
.\Start_AI_Desk_Phone.bat
```

打开后台页面：

```text
http://127.0.0.1:8765/
```

没有硬件时打开模拟页：

```text
http://127.0.0.1:8765/simulator
```

## 提醒节奏

```text
触发：POST http://127.0.0.1:8765/api/ai/hook
响铃：1 秒响 -> 4 秒停 -> 循环
灯光：LED 与蜂鸣器同步，1 秒亮 -> 4 秒灭 -> 循环
停止：摘机接听，或在页面点击“停止提醒”
超时：约 90 秒无人接听后停止普通响铃，切换忙音节奏
```

回话播放行为：

- 输入法模式下，播报中挂机会停止本次播放。
- Agent 模式下，播报中挂机会暂停本次回报并重新进入回拨；用户再摘机后继续播报。
- 用户语音提交后再挂机，不会取消后台处理；处理完成后会回拨。

## 单独测试 hook 脚本

```powershell
.\.venv\Scripts\python.exe tools\codex_operator_hook.py
```

如果控制台在线，脚本会读取回拨开关状态，然后把完成消息发送到本地 hook 地址。控制台会把消息加入回话队列，并启动提醒。

## HTTP 调用

最小请求：

```http
POST http://127.0.0.1:8765/api/ai/hook
Content-Type: application/json

{
  "source": "codex",
  "text": "首长，任务已经完成。"
}
```

控制台会依次尝试读取这些字段：

```text
text
reply
summary
message
```

没有文本时不会入队。

兼容路径：

```text
POST http://127.0.0.1:8765/hook
```

## Codex 仓库本地 hook

仓库提供：

```text
.codex/hooks.json
.codex/hooks/ai_desk_phone_stop_hook.py
```

Codex 的 `Stop` 事件会在回合结束时调用 hook。启用 hook 后，在 Codex 里用 `/hooks` 查看并信任这条项目 hook。

信任后，Codex 完成回合时会调用：

```text
tools/codex_operator_hook.py
```

脚本先读取：

```text
GET http://127.0.0.1:8765/api/replies
```

如果 `callback_enabled` 是 `false`，脚本会跳过发送，不影响 Codex 正常结束。控制台接收端也会再次检查“完成后电话回拨”开关；关闭时即使外部误发 hook，也不会入队、不响铃。

## 角色模型和密钥

语音 ASR/TTS 使用：

```text
VOLCENGINE_API_KEY
```

通讯员润色和 Agent 角色回复使用：

```text
ARK_API_KEY
ARK_CHAT_COMPLETIONS_ENDPOINT=https://ark.cn-beijing.volces.com/api/v3/chat/completions
DOUBAO_OPERATOR_POLISH_ENABLED=true
DOUBAO_OPERATOR_MODEL=doubao-seed-character-260628
DOUBAO_OPERATOR_MAX_TOKENS=900
DOUBAO_OPERATOR_USE_SPEECH_API_KEY=false
```

两类 key 可以不同。不要把 `.env` 提交到仓库。

## 可选环境变量

默认 hook 地址：

```text
http://127.0.0.1:8765/api/ai/hook
```

需要改地址时：

```powershell
$env:AI_DESK_PHONE_HOOK_URL="http://127.0.0.1:8765/api/ai/hook"
$env:AI_DESK_PHONE_SOURCE="codex"
```

脚本请求失败时会直接跳过并返回成功码，避免电话控制台没开时影响 Codex 正常结束。

## 语音调试接口

检查语音服务状态：

```text
GET http://127.0.0.1:8765/api/speech/status
```

测试语音识别文件接口：

```text
POST http://127.0.0.1:8765/api/speech/transcribe-file
{"path":"C:\\path\\to\\audio.wav"}
```

测试录音会话：

```text
POST http://127.0.0.1:8765/api/voice/start
POST http://127.0.0.1:8765/api/voice/stop
GET  http://127.0.0.1:8765/api/voice/status
```

在 `business_mode = doubao` 时，电话 `RELEASED` 会进入摘机通话；后台用静音检测自动截断一轮语音并调用豆包 ASR。
如果电话仍然保持摘机，识别结果会按配置直接播放或入队；如果用户说完后 `PRESSED` 挂机，当前录音/识别会继续在后台完成，完成后通过回话队列回拨。只有 AI 正在播报时挂机，才会立即停止当前播报并结束这次语音回话。

## 抬起 / 按下信号

通信协议保持原来的接口，不改字段和路径：

```text
POST http://127.0.0.1:8765/api/simulate/release
POST http://127.0.0.1:8765/api/simulate/press
```

业务层解释为：

```text
RELEASED = 抬起
PRESSED  = 按下
```

接听、停止播放、快捷键触发都由这两个已有信号派生。页面可以显示“抬起 / 按下”，但不改变底层通信协议。

## 在 Codex 里配置 hooks

本仓库已经提供 repo-local hooks 配置：

```text
.codex/hooks.json
.codex/hooks/ai_desk_phone_stop_hook.py
```

Codex 的 `Stop` 事件会在当前回合停止时调用 hook。首次启用或修改 hook 后，需要在 Codex 中使用
`/hooks` 查看并信任这条项目 hook。信任后，Codex 完成当前回合时会调用
`tools/codex_operator_hook.py`，把完成消息发送到本地控制台。

这个 hook 会先读取：

```text
GET http://127.0.0.1:8765/api/replies
```

如果 `callback_enabled` 为 `false`，脚本会直接跳过，不会发送完成消息。控制台接收端也会再次检查
“完成后电话回拨”开关；如果开关关闭，即使外部误发了 `/api/ai/hook`，也会丢弃，不入队、不响铃。

当前本机 Codex 还配置了全局 `notify` 包装脚本：

```text
.codex/hooks/ai_desk_phone_notify.py
```

它会先调用 Codex 原来的 `codex-computer-use.exe turn-ended` 通知，再在当前工作目录属于
`AiLandLine` 仓库时调用 `tools/codex_operator_hook.py`。这样 Codex 桌面端结束一轮任务时，
即使没有手动进入 `/hooks` 信任 repo-local Stop hook，也会触发本地电话回拨；其他项目不会误触发电话。
本机全局配置改在 `C:\Users\Damue\.codex\config.toml`，原配置备份为
`C:\Users\Damue\.codex\config.toml.ai_desk_phone_hook_backup_20260709`。

## 可选环境变量

默认 hook 地址是：

```text
http://127.0.0.1:8765/api/ai/hook
```

需要改地址时设置：

```powershell
$env:AI_DESK_PHONE_HOOK_URL="http://127.0.0.1:8765/api/ai/hook"
$env:AI_DESK_PHONE_SOURCE="codex"
```

脚本请求失败时会直接跳过并返回成功码，避免电话控制台没开时影响 Codex 正常结束。

## Hook 请求体

最小请求体：

```json
{
  "source": "codex"
}
```

携带回话文本：

```json
{
  "source": "codex",
  "text": "首长，任务已经完成。"
}
```

控制台会依次尝试读取 `text`、`reply`、`summary`、`message` 字段。没有文本时不会入队。
