# 控制指挥中心页面

这是 AI 桌面电话的控制指挥中心，用来承载地球屏保、真实电话信号和当前 Agent 阶段。日常使用时由 `Start_AI_Desk_Phone.bat` 启动，并通过 Python 控制台托管。

```text
http://127.0.0.1:8765/command-center/
```

老控制台仍保留在 `http://127.0.0.1:8765/`，用于配置、调试和排障。

页面也可以作为静态视觉稿单独运行：

```powershell
python -m http.server 8790 --bind 127.0.0.1 --directory web\variant-earth-command-center
```

打开：

```text
http://127.0.0.1:8790/
```

真实地图探索页：

```text
http://127.0.0.1:8790/real-map.html
```

## 页面定位

当前方向是 Agent 模式的待命屏，不是调试仪表盘。地球负责表达通信覆盖和待命氛围；界面只显露一个高层状态，不直接展示 GPIO、ADC、ASR chunk、控制台连接等工程字段。

## 状态模型

- `等待命令中`：挂机、无任务、无回话队列。
- `接收指令中`：摘机或录音中。
- `执行命令中`：语音已提交，ASR、后台任务或 Agent 执行中。
- `等待接听中`：任务完成，有回话待播报或正在回拨。
- `播报结果中`：用户已接听，正在播报回话。

给页面接口传入的状态 key 是：

```text
waiting
listening
executing
feedback
reporting
```

## Agent 调用接口

页面加载后会暴露同一个桥接对象，推荐使用 `window.AILandline`：

```js
window.AILandline
window.AiLandLineConsole
window.AiLandLine
window.AIDeskPhone
```

常用状态控制：

```js
window.AILandline.setPhase("waiting")
window.AILandline.setPhase("listening")
window.AILandline.setPhase("executing")
window.AILandline.setPhase("feedback")
window.AILandline.setPhase("reporting")
```

直接跳转到城市或位置：

```js
window.AILandline.focusCity("北京")
window.AILandline.focusPlace("上海")

window.AILandline.flyTo({
  lng: 116.4074,
  lat: 39.9042,
  label: "北京",
  zoom: 9
})
```

返回地球屏保：

```js
window.AILandline.showGlobe({ phase: "waiting" })
```

查询可用能力：

```js
window.AILandline.getState()
window.AILandline.getCities()
window.AILandline.getPhases()
```

## 事件与跨窗口调用

同页面脚本可以用事件驱动：

```js
window.dispatchEvent(new CustomEvent("ailandline:phase", {
  detail: "executing"
}))

window.dispatchEvent(new CustomEvent("ailandline:navigate", {
  detail: {
    lng: 116.4074,
    lat: 39.9042,
    label: "北京",
    zoom: 9,
    requestId: "nav-001"
  }
}))

window.dispatchEvent(new CustomEvent("ailandline:command", {
  detail: {
    action: "focusCity",
    payload: "上海",
    requestId: "nav-002"
  }
}))
```

如果页面在 iframe 或另一个窗口里，使用 `postMessage`：

```js
iframe.contentWindow.postMessage({
  channel: "AiLandLineConsole",
  action: "navigate",
  payload: {
    lng: 116.4074,
    lat: 39.9042,
    label: "北京",
    zoom: 9
  },
  requestId: "nav-003"
}, "http://127.0.0.1:8765")
```

页面会发出 `ailandline:result` 事件，调用方可以监听结果：

```js
window.addEventListener("ailandline:result", (event) => {
  console.log(event.detail)
})
```

## URL 直接跳转

Agent 也可以直接打开带参数的 URL，让页面启动后自动进入对应位置：

```text
http://127.0.0.1:8765/command-center/?city=北京
http://127.0.0.1:8765/command-center/?place=上海
http://127.0.0.1:8765/command-center/?lng=116.4074&lat=39.9042&label=北京&zoom=9
```

可选视觉参数：

```text
fx
visual
preset
```

## 控制台实时数据

页面会自动连接本机控制台事件流：

```js
new EventSource("http://127.0.0.1:8765/events")
```

主要事件映射：

```text
sample.python_state === "RELEASED" -> listening
voice_status.recording -> listening
voice_status.processing 或 streaming_asr -> executing
reply_status.queue_size > 0 或 pending_report_text -> feedback
reply_status.playback_active -> reporting
其他空闲状态 -> waiting
```

Agent skill 会通过同一个事件流把地球操作投递给页面：

```json
{
  "type": "command_center_command",
  "command": {
    "source": "agent",
    "skill": "command_center.earth",
    "action": "focusCity",
    "payload": "北京",
    "options": { "zoom": 11.8 }
  }
}
```

页面收到后会复用现有桥接命令执行，不需要刷新页面。

最小 Agent 文字入口：

```http
POST /api/agent/turn
Content-Type: application/json

{
  "source": "codex",
  "text": "定位北京",
  "reply_behavior": "direct"
}
```

可直接调用的本机控制命令：

```js
window.AILandline.startAgent()
window.AILandline.stopVoice()
window.AILandline.stopPlayback()
window.AILandline.clearAlert()
window.AILandline.simulateRelease()
window.AILandline.simulatePress()
window.AILandline.beep()
window.AILandline.ringOn()
window.AILandline.ringOff()
window.AILandline.ledOn()
window.AILandline.ledOff()
```

底层 HTTP 接口仍然可直接调用：

```text
POST /api/agent/start
POST /api/agent/turn
POST /api/voice/stop
POST /api/playback/stop
POST /api/alert/clear
POST /api/simulate/release
POST /api/simulate/press
POST /api/hardware/beep
POST /api/hardware/ring_on
POST /api/hardware/ring_off
POST /api/hardware/led_on
POST /api/hardware/led_off
POST /api/ai/hook
```

## 视觉原则

- 常态使用青绿色和暖白，表达服务在线、链路稳定。
- 红色只作为告警色，不再作为页面主色。
- UI 面板贴边放置，尽量不遮挡地球。
- 地球上的英文字符效果保持参考页面风格，UI 文案全部使用中文。

## 真实地图方向

- `index.html` 是当前主入口：融合 Three.js 漂亮地球和 MapLibre 真实卫星地图，不再把地球态和地图态拆成两个独立体验。
- `real-map.html` 保留为真实地图探索页，后续只作为对照和备份。
- 默认地球不在边缘添加轨道、圆环、地图框等装饰，保留参考页的稀疏地表短词和上半弧文字效果。
- Three.js 地球保留拖拽旋转、惯性旋转、滚轮 / 双指缩放；超过清晰阈值后进入 MapLibre 卫星地图。
- 卫星地图使用 EOX Sentinel-2 cloudless 2024 瓦片，并叠加克制的城市标签，避免放大后不知道当前位置。
- 云层暂不启用；此前测试的 GIBS 云量图层是科学配色，不适合作为默认产品视觉。
