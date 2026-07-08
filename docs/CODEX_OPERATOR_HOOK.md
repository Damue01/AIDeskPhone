# Codex 接线员 hook 配置

接线员模式用于“文字输入 / Codex 任务完成提醒”。Codex 完成一次任务后，只发一个轻量
hook 到电脑端控制台；控制台再通过 Wi-Fi 或 USB 串口命令 ESP32-C3 驱动蜂鸣器和 LED。

ESP32-C3 不发送网页，也不画页面。它只负责 GPIO 状态上报和执行硬件命令。

## 提醒节奏

```text
触发：Codex 任务结束 -> POST http://127.0.0.1:8765/api/ai/hook
响铃：1 秒响 -> 4 秒停 -> 循环
灯光：整个提醒期间 LED 保持点亮
停止：摘机接听，或在页面点击“停止提醒”
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

如果控制台和 ESP32 都在线，页面会进入“接线员模式”：LED 亮，蜂鸣器按 `1 秒响 -> 4 秒停`
循环。摘机或点击“停止提醒”后关闭。

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
