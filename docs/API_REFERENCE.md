# AI Desk Phone API 参考

本文记录当前 `hg113-main` 的本地 HTTP、SSE、ESP32 命令协议和指挥中心 JavaScript Bridge。用户安装、接线与排障见[标准参考手册](REFERENCE_MANUAL.md)。

> [!CAUTION]
> API 没有身份认证。默认 HTTP 地址是 `http://127.0.0.1:8765`，只适合本机和受信任环境。服务端拒绝跨站浏览器 API 请求，POST 必须使用 `application/json`；这些防护不能替代身份认证。不要反向代理到公网，也不要把 `--host` 改为 `0.0.0.0` 后接入不受信任网络。

## 1. 约定

- 默认 Base URL：`http://127.0.0.1:8765`
- JSON 请求：`Content-Type: application/json`
- JSON 成功响应通常包含 `"ok": true`，状态查询直接返回状态对象。
- 失败响应通常包含 `"ok": false` 和 `"error"`，HTTP 状态码不应作为唯一判断依据。
- API 当前没有版本前缀，未承诺跨大版本稳定；集成方应容忍未知字段。
- 所有时间、队列和设备状态均以控制台进程内状态为准。

## 2. 页面与事件入口

| 路径 | 方法 | 用途 |
| --- | --- | --- |
| `/` | GET | 配置与调试控制台 |
| `/command-center/` | GET | 地球指挥中心 |
| `/simulator` | GET | 无硬件模拟台 |
| `/events` | GET | Server-Sent Events |

## 3. HTTP API

### 3.1 GET

| 路径 | 用途 |
| --- | --- |
| `/api/config` | 当前控制台配置 |
| `/api/action-presets` | 快捷键动作预设 |
| `/api/simulation` | 模拟器状态 |
| `/api/hardware/status` | 串口、UDP、TCP、引脚和当前样本 |
| `/api/firmware/status` | 固件烧录后台任务状态 |
| `/api/replies` | 待播报、当前播报和已完成回复 |
| `/api/speech/status` | ASR、TTS、Ark 配置可用性 |
| `/api/speech/speakers` | 可用 TTS 音色；可带 `resource_id` 查询参数 |
| `/api/voice/status` | 录音、流式 ASR 和当前语音轮次 |
| `/api/agent/status` | Agent session、工具、skill 和最近结果 |
| `/api/device/next-command` | 设备轮询兼容入口；不建议新集成使用 |

硬件状态示例：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/hardware/status
```

调用方不应依赖完整响应字段顺序。判断实机是否可用时，优先检查：

```text
real_device_connected
hardware_io_enabled
current_sample
udp_last_seen_seconds
tcp_command_client
serial_connected
```

### 3.2 POST：配置

| 路径 | 请求重点 | 用途 |
| --- | --- | --- |
| `/api/config` | 配置字段对象 | 更新并保存控制台配置 |
| `/api/business-mode` | `business_mode` | 切换输入法/Codex 与 Agent 模式 |
| `/api/hook-scheme` | `hook_scheme` | 切换摘挂机解释方向 |
| `/api/speech/config` | 语音环境变量字段 | 更新 `.env` 中的语音/模型配置 |

`/api/speech/config` 会写入本机 `.env`。不要从不受信任网页或远端服务调用。

### 3.3 POST：Hook 与 Agent

| 路径 | 请求重点 | 用途 |
| --- | --- | --- |
| `/api/ai/hook` | `source`、完成文本 | 把外部任务结果加入电话回拨流程 |
| `/hook` | 同上 | 兼容别名 |
| `/api/agent/start` | `reason` | 开始 Agent 语音录音 |
| `/api/agent/turn` | `text`、`source`、`reply_behavior` | 提交 Agent 文本轮次 |
| `/api/agent/session/new` | 可选 `reason` | 创建新 Agent session |
| `/api/agent/session/delete` | 可选 `session_id`、`reason` | 删除当前 Agent session，并创建空 session |

#### AI Hook

```http
POST /api/ai/hook
Content-Type: application/json

{
  "source": "codex",
  "text": "固件构建和测试已经通过。"
}
```

控制台会从常见文本字段中提取完成内容，并拒绝空文本或通用占位文本。可靠集成应始终显式发送 `text`。

PowerShell：

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8765/api/ai/hook `
  -Method Post `
  -ContentType 'application/json' `
  -Body '{"source":"manual","text":"任务已经完成。"}'
```

#### Agent 文本轮次

```http
POST /api/agent/turn
Content-Type: application/json

{
  "source": "manual",
  "text": "定位上海",
  "reply_behavior": "direct"
}
```

`reply_behavior` 的常见值：

| 值 | 行为 |
| --- | --- |
| `direct` | 按当前摘挂机状态立即播放或进入回拨 |
| `callback` | 进入回拨队列 |
| `silent` | 只记录，不播报 |

`none` 是 `silent` 的兼容别名。删除 session 时若提供 `session_id`，服务端会把它作为并发保护条件；它与当前 `conversation_id` 不一致时返回 `session_mismatch`，不会误删当前 session。接口不支持按任意历史 ID 删除 session。

### 3.4 POST：语音、回复和内存

| 路径 | 用途 |
| --- | --- |
| `/api/voice/start` | 开始录音和流式 ASR |
| `/api/voice/stop` | 停止录音并按请求决定是否提交 |
| `/api/speech/transcribe-file` | 转写服务器本机可访问的音频文件 |
| `/api/replies` | 手动加入一条回复 |
| `/api/replies/clear` | 清空回复队列 |
| `/api/playback/stop` | 停止当前播报 |
| `/api/memory/clear` | 清理回拨、语音和 Agent 运行时记忆 |
| `/api/alert/clear` | 停止 LED/蜂鸣器提醒 |

`/api/speech/transcribe-file` 接收的是控制台主机路径，不是浏览器文件上传接口。

### 3.5 POST：模拟与硬件

| 路径 | 用途 |
| --- | --- |
| `/api/simulation` | 启用或关闭模拟样本 |
| `/api/simulate/press` | 模拟 `PRESSED`，即挂机 |
| `/api/simulate/release` | 模拟 `RELEASED`，即摘机 |
| `/api/hardware/beep` | 蜂鸣器短响 |
| `/api/hardware/ring_on` | 持续响铃 |
| `/api/hardware/ring_off` | 停止响铃 |
| `/api/hardware/led_on` | 点亮 LED |
| `/api/hardware/led_off` | 熄灭 LED |
| `/api/hardware/pins` | 更新测试引脚并下发设备 |
| `/api/firmware/upload` | 后台烧录当前 ESP32-S3 固件环境 |

蜂鸣器示例：

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8765/api/hardware/beep `
  -Method Post `
  -ContentType 'application/json' `
  -Body '{}'
```

硬件接口可能在命令已发送但没有及时收到状态确认时返回失败。调用后应再次读取 `/api/hardware/status`，不要只依赖发送结果。使用 `--simulation-only` 启动时，`hardware_io_enabled=false`；硬件命令只更新模拟状态，固件烧录会被拒绝。

## 4. Server-Sent Events

连接：

```js
const events = new EventSource("http://127.0.0.1:8765/events");

events.onmessage = (event) => {
  const message = JSON.parse(event.data);
  console.log(message.type, message);
};
```

常见事件类型包括：

| `type` | 内容 |
| --- | --- |
| `state`、`sample` | 摘挂机和设备样本 |
| `hardware_status` | 硬件链路状态 |
| `voice_status` | 录音和 ASR 状态 |
| `reply_status` | 回复队列和播放状态 |
| `agent_status` | Agent 当前轮次、工具和 session |
| `firmware_status` | 固件烧录进度 |
| `command_center_command` | 发给指挥中心的页面动作 |

事件集合会随实现增长。客户端应忽略未知 `type`，并在断线后依赖 `EventSource` 自动重连，再通过 GET 状态接口补齐快照。

## 5. ESP32 通信协议

### 5.1 端口和方向

| 传输 | 方向 | 用途 |
| --- | --- | --- |
| UDP 8766 | ESP32 → PC 广播 | JSON 遥测和心跳 |
| UDP 8767 | PC → ESP32 | 单行命令 |
| TCP 8768 | ESP32 → PC 建连 | 持久双向命令通道 |
| USB 115200 | 双向 | 串口日志和命令 |

设备约每 250 ms 发送样本，并每秒发送 heartbeat。桌面端把超过约 8 秒未更新的设备视为不新鲜。

### 5.2 遥测示例

```json
{
  "type": "sample",
  "device": "ailandline-c3",
  "seq": 42,
  "hook_pin": 4,
  "buzzer_pin": 2,
  "led_pin": 1,
  "digital": "LOW",
  "hook": "OFF_HOOK",
  "buzzer": "OFF",
  "led": "OFF",
  "wifi_connected": true,
  "wifi_ip": "192.168.1.50",
  "wifi_rssi": -55
}
```

`device = "ailandline-c3"` 是历史兼容值，不能用于判断实际芯片型号。当前主固件环境仍是 ESP32-S3。

主固件的 `adc` 为数字电平生成的 0/4095 合成值，并带有 `adc_synthetic = true`。

### 5.3 命令

命令可以是纯文本，也可以是包含 `type` 的单行 JSON。

```text
ping
beep
ring_on
ring_off
led_on
led_off
provision
```

```json
{
  "type": "set_pins",
  "hook_pin": 4,
  "buzzer_pin": 2,
  "led_pin": 1
}
```

```json
{
  "type": "config",
  "hook_pin": 4,
  "buzzer_pin": 2,
  "led_pin": 1,
  "sample_interval_ms": 250,
  "debounce_ms": 120
}
```

固件把命令转为小写后匹配；自定义集成不应依赖大小写保留。

## 6. 指挥中心 JavaScript Bridge

页面加载后暴露以下兼容对象，它们指向同一套能力：

```js
window.AILandline
window.AiLandLineConsole
window.AiLandLine
window.AIDeskPhone
```

推荐新代码使用 `window.AILandline`。其他名称仅用于兼容历史调用方。

### 6.1 Ready

```js
window.addEventListener("ailandline:ready", (event) => {
  console.log(event.detail);
});

if (window.AILandline?.ready) {
  console.log(window.AILandline.getState());
}
```

### 6.2 常用方法

| 方法 | 用途 |
| --- | --- |
| `pushLog(message)` | 追加一条页面日志 |
| `setPhase(phase)` | 设置高层阶段 |
| `setSource(source)` | 设置页面来源文字 |
| `focusCity(city, options)` | 跳转到内置城市 |
| `flyTo(target, options)` | 跳转经纬度 |
| `focusPlace(place, options)` | 跳转地点对象 |
| `showGlobe(options)` | 返回地球视图 |
| `getState()` | 获取页面状态 |
| `getCities()` | 获取内置城市 |
| `getPhases()` | 获取阶段列表 |
| `setVisualPreset(preset)` | 设置视觉预设 |
| `cycleVisualPreset(step)` | 切换视觉预设 |
| `getVisualPresets()` | 获取视觉预设列表 |
| `invoke(action, payload, options)` | 统一动作入口 |

阶段值：

```text
waiting
listening
executing
feedback
reporting
```

示例：

```js
window.AILandline.setPhase("executing");
window.AILandline.focusCity("上海", { zoom: 11.8 });
window.AILandline.flyTo({
  lng: 121.4737,
  lat: 31.2304,
  label: "上海",
  zoom: 9
});
window.AILandline.showGlobe({ phase: "waiting" });
```

直接方法返回普通对象，成功时包含 `ok: true`，失败时包含 `ok: false` 和 `reason`。

### 6.3 CustomEvent

```js
window.addEventListener("ailandline:result", (event) => {
  console.log(event.detail.result);
});

window.dispatchEvent(new CustomEvent("ailandline:command", {
  detail: {
    requestId: "cmd-001",
    action: "focusCity",
    payload: "北京",
    options: { zoom: 11.8 }
  }
}));
```

页面还接受 `ailandline:log`、`ailandline:phase` 和 `ailandline:navigate`。

### 6.4 postMessage

```js
window.addEventListener("message", (event) => {
  const data = event.data;
  if (data?.channel === "AiLandLineConsole" && data.type === "ailandline:result") {
    console.log(data.requestId, data.result);
  }
});

window.postMessage({
  channel: "AiLandLineConsole",
  type: "ailandline:command",
  requestId: "nav-001",
  action: "focusCity",
  payload: "上海",
  options: { zoom: 12 }
}, window.location.origin);
```

`postMessage` 只适合受信任的本地页面上下文。嵌入 iframe 时，调用方应自行校验 `event.origin`。

### 6.5 URL 参数

```text
/command-center/?city=北京
/command-center/?lng=121.4737&lat=31.2304&label=上海&zoom=9
/command-center/?fx=cinematic
```

页面需要联网加载 MapLibre、OpenFreeMap/Esri 图层以及 EOX/NASA 影像。网络不可用时，本地地球纹理仍可作为降级画面，但真实地图和实时影像会受限。

## 7. 兼容与变更规则

- 新字段可以添加到状态对象，客户端必须忽略未知字段。
- 删除或重命名公开路径前，应先保留一个发布周期的兼容别名。
- `AiLandLine*` JavaScript 名称、`ailandline-c3` 设备字符串和主固件目录名属于历史兼容项。
- 接口行为变更必须同步更新本文、对应测试和 `CHANGELOG.md`。
