# 地球指挥中心页面

这个目录是 AI Desk Phone 的电话主屏。日常使用时不用单独部署，`Start_AI_Desk_Phone.bat` 会启动 Python 控制台，并把这个页面托管到：

```text
http://127.0.0.1:8765/command-center/
```

后台配置页在：

```text
http://127.0.0.1:8765/
```

## 页面负责什么

地球指挥中心只负责“看见电话和 Agent 当前在做什么”。它不保存核心状态，也不直接处理 ASR、TTS、硬件和工具调用。

它负责：

- 展示地球屏保和真实卫星地图。
- 展示高层阶段：等待命令、接收指令、执行命令、等待接听、播报结果。
- 接收后端 `/events` 事件流。
- 执行 Agent 发来的地图动作：定位城市、跳转经纬度、回到地球首页。
- 暴露一个页面桥接对象，方便本地 Agent 或调试脚本调用。

后端控制台负责：

- 读取 ESP32-C3 状态。
- 录音、ASR、TTS 和回拨队列。
- Agent session、工具调用和 skill 加载。
- 蜂鸣器和 LED 命令。

## 主要文件

| 文件 | 作用 |
| --- | --- |
| `index.html` | 当前主入口，融合 Three.js 地球和 MapLibre 卫星地图。 |
| `real-map.html` | 真实地图探索页，保留作调试和对照。 |
| `API.md` | 页面桥接对象、事件、postMessage 和 URL 参数的详细 API。 |
| `assets/` | 地球纹理图片。 |
| `vendor/three.module.js` | 本地 Three.js 依赖，避免页面启动时依赖外部 CDN。 |

## 阶段模型

页面显示中文阶段，接口使用英文 key：

| 中文 | key | 含义 |
| --- | --- | --- |
| 等待命令中 | `waiting` | 挂机、无任务、无待播报回话。 |
| 接收指令中 | `listening` | 用户摘机或正在录音。 |
| 执行命令中 | `executing` | 语音已提交，ASR、工具或 Agent 正在处理。 |
| 等待接听中 | `feedback` | 任务完成，电话正在回拨或队列里有回话。 |
| 播报结果中 | `reporting` | 用户已接听，正在 TTS 播报。 |

页面会根据 `/events` 中的硬件、语音、回拨和 Agent 状态自动切换阶段。

## Agent 怎么操作页面

Agent 不直接改 DOM。它通过后端事件流发布命令：

```json
{
  "type": "command_center_command",
  "command": {
    "source": "agent",
    "skill": "command_center.earth",
    "action": "focusCity",
    "payload": "上海",
    "options": {
      "zoom": 11.8
    }
  }
}
```

页面收到后调用自己的桥接能力执行动作。

## 页面桥接对象

页面加载后会暴露：

```js
window.AILandline
window.AiLandLineConsole
window.AiLandLine
window.AIDeskPhone
```

常用方法：

```js
window.AILandline.setPhase("executing")
window.AILandline.focusCity("上海")
window.AILandline.flyTo({ lng: 121.4737, lat: 31.2304, label: "上海", zoom: 9 })
window.AILandline.showGlobe({ phase: "waiting" })
window.AILandline.getState()
```

完整接口见 [API.md](API.md)。

## 直接测试

主项目启动后，可以提交一轮 Agent 文本：

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8765/api/agent/turn `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"source":"manual","text":"定位上海","reply_behavior":"direct"}'
```

也可以直接用 URL 参数打开指定位置：

```text
http://127.0.0.1:8765/command-center/?city=北京
http://127.0.0.1:8765/command-center/?lng=121.4737&lat=31.2304&label=上海&zoom=9
```

如果只想看静态页面，不启动电话控制台：

```powershell
python -m http.server 8790 --bind 127.0.0.1 --directory web\variant-earth-command-center
```

然后打开：

```text
http://127.0.0.1:8790/
```

静态运行只能看视觉和页面 API，不能连接真实电话、ASR、TTS、Agent session 或硬件命令。

## 视觉原则

- 地球页是待命主屏，不是调试仪表盘。
- 调试数据放后台控制台，地球页只显示高层状态。
- UI 尽量不遮挡地球和地图。
- 中文界面优先，内部 action/key 保持英文，方便 Agent 和脚本调用。
