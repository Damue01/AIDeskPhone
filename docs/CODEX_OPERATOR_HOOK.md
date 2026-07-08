# Codex 接线员 hook 配置

接线员模式用于“文字输入 / Codex 任务完成提醒 / 回话”。Codex 完成一次任务后，发送一个轻量
hook 到电脑端控制台；控制台把 hook 文本加入回话队列，再通过 Wi-Fi 或 USB 串口命令 ESP32-C3
驱动蜂鸣器和 LED。

ESP32-C3 不发送网页，也不画页面。它只负责 GPIO 状态上报和执行硬件命令。

## 提醒节奏

```text
触发：Codex 任务结束 -> POST http://127.0.0.1:8765/api/ai/hook
响铃：1 秒响 -> 4 秒停 -> 循环
灯光：LED 与蜂鸣器同步，1 秒亮 -> 4 秒灭 -> 循环
停止：摘机接听，或在页面点击“停止提醒”
回话：摘机后按队列顺序播放；播放中挂机会立即停止当前播放
超时：约 90 秒无人接听后停止普通响铃，切换忙音节奏
```

## 先启动控制台

```powershell
.\Start_AI_Desk_Phone.bat
```

或手动启动：

```powershell
.\.venv\Scripts\python.exe tools\ai_desk_phone_console.py --port COM5
```

打开页面确认能看到设备：

```text
http://127.0.0.1:8765/
```

## 单独测试 hook 脚本

```powershell
.\.venv\Scripts\python.exe tools\codex_operator_hook.py
```

如果控制台和 ESP32 都在线，页面会进入“接线员模式”：蜂鸣器和 LED 同步按
`1 秒响/亮 -> 4 秒停/灭` 循环。摘机或点击“停止提醒”后关闭。

## 手动模拟页

没有真实设备或真实 Codex 输出时，可以打开：

```text
http://127.0.0.1:8765/simulator
```

这个页面可以手动：

- 模拟抬起 / 按下。
- 写入一条回话并加入队列。
- 模拟一次 Codex hook。
- 配置摘机快捷键、挂机快捷键、回话开关和本地语音播放。

## 豆包 TTS 2.0 / ASR

语音配置不写进仓库。复制 `.env.example` 为 `.env`，再填入本机密钥：

```text
VOLCENGINE_API_KEY=...
DOUBAO_TTS_SPEAKER=zh_female_tianmeitaozi_uranus_bigtts
```

如果使用旧版语音控制台凭据，再改填 `VOLCENGINE_APP_ID` 和 `VOLCENGINE_ACCESS_TOKEN`。
新版 API Key 存在时，控制台会优先使用新版 `X-Api-Key` 接入。

回话播放时，控制台会优先调用豆包 TTS 2.0 生成音频；如果 `.env` 未配置或请求失败，会回退到 Windows TTS / 模拟播放。

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
如果电话仍然保持摘机，识别结果会直接播放回话；`PRESSED` 只表示挂机/取消，会停止录音、停止播放并丢弃本轮语音回话。

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

## 在 Codex 里配置 notify

编辑：

```text
%USERPROFILE%\.codex\config.toml
```

加入或修改这一行：

```toml
notify = [
  "C:\\Users\\Damue\\Documents\\AiLandLine\\.venv\\Scripts\\python.exe",
  "C:\\Users\\Damue\\Documents\\AiLandLine\\tools\\codex_operator_hook.py"
]
```

注意：如果 `config.toml` 里已经有 `notify = [...]`，先备份原来的那一行。Codex 通常只会使用
一条 notify 命令；直接覆盖会让原来的通知命令失效。

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

控制台会依次尝试读取 `text`、`reply`、`summary`、`message`、`codex_payload` 字段。没有文本时，
会生成一条默认回话。
