# HG113 产品方案

这份文档说明 AI Desk Phone 的产品形态。它不是硬件焊接教程；硬件细节看 [HG113 连接方式](HG113_CONNECTION_MANUAL.md)，实际制作步骤看 [制作与维护手册](BUILD_MANUAL.md)。

## 一句话说明

把 HG113 电话改造成一个本地 AI 服务终端：用户拿起听筒说话，电脑端 Agent 识别、执行工具、生成回话；如果用户已经挂机，任务继续在后台完成，然后用蜂鸣器和 LED 回拨提醒。

仓库提供软件、固件、页面、Agent runtime 和参考接线方案。真实座机外壳、听筒音频、供电、走线、焊接和绝缘需要用户自己完成。

## 产品边界

产品包含：

- 用 ESP32-C3 读取 HG113 摘挂机状态。
- 用 Wi-Fi UDP 把电话状态送到电脑端控制台。
- 由电脑端控制台管理配置、日志、录音、ASR、TTS、Agent、回拨队列和硬件命令。
- 用 ESP32-C3 驱动蜂鸣器和 LED。
- 用蓝牙音频模块把听筒接成 Windows 麦克风和扬声器。
- 用地球指挥中心页面展示待命、接收指令、执行命令、等待接听和播报状态。

产品边界：

- 不接电话外线。
- 不让 ESP32-C3 处理音频。
- 不让 ESP32-C3 托管网页。
- BLE HID 不作为主控制链路。
- 不从全局 PI 或外部插件目录加载大量 skills。

## 系统组成

```text
HG113 摘挂机结构
  -> ESP32-C3 GPIO0
  -> Wi-Fi UDP 遥测
  -> 电脑端控制台
  -> Agent / 回拨队列 / 指挥中心页面
  -> ESP32-C3 GPIO21 蜂鸣器、GPIO20 LED

HG113 听筒
  -> 蓝牙音频模块
  -> Windows 麦克风和扬声器
  -> 豆包 ASR / TTS
```

ESP32-C3 只做低层硬件桥。业务判断、AI、地图、工具调用和回话都在电脑端。

## 两种使用模式

### 输入法模式

电话像一个实体快捷键和任务提醒器。

```text
摘机 -> 触发目标软件的开始输入动作
挂机 -> 触发目标软件的结束/提交动作
Codex 完成 -> 调用 /api/ai/hook -> 电话回拨提醒
```

这个模式适合 Codex、聊天框、编辑器或其他已经能自己执行任务的应用。AI Desk Phone 只负责输入动作和完成提醒。

### Agent 模式

电话本身就是 AI 终端。

```text
摘机 -> 开始录音和流式 ASR
停顿或挂机 -> 提交一轮 user turn
Agent -> 调用本地工具、地图 skill、搜索、命令或文件只读工具
完成 -> 生成通讯员回话
挂机状态 -> 蜂鸣器和 LED 回拨
摘机接听 -> TTS 播报
```

挂机不会取消已提交的后台任务。Agent 播报中挂机会暂停本次回话并重新进入回拨，用户再摘机后继续播报。

## Agent 方案

Agent runtime 采用 PI 风格的最小结构：

- session 以 JSONL 写入 `data/agent_sessions/`。
- 每轮保存 user 消息、assistant toolCall、toolResult 和最终 assistant 回话。
- prompt 由 system、developer、工具列表、skills、压缩摘要和最近消息组成。
- 上下文变长后自动压缩较早消息，保持最近消息。
- 后台页面能查看 session、prompt、loaded skills、tools 和压缩摘要。

只加载项目本地 skill：

```text
.pi/skills/command-center-earth/SKILL.md
```

这个 skill 负责告诉 Agent 如何操作地球/地图页面，例如定位城市、跳转经纬度、返回地球首页和切换页面阶段。

## 工具范围

最小 Agent 具备这些基础能力：

- 地图和地球控制。
- 天气摘要、浏览器搜索、打开 URL。
- 项目内只读文件工具：`read`、`grep`、`find`、`ls`。
- allowlist 应用启动。
- 用户明确要求时执行受限 shell 命令。

暂时不加入文件写入、多 Agent 分工、任意外部 skill 加载和高风险自动化。语音控制下，工具越少越容易调试，也更容易知道 Agent 到底做了什么。

## 回拨和播报

回拨队列是全局能力，输入法模式和 Agent 模式都能用。

```text
任务完成
  -> 生成回话
  -> 加入队列
  -> LED 和蜂鸣器按 1 秒响/亮、4 秒停/灭循环
  -> 用户摘机
  -> 按队列播报
```

约 90 秒无人接听后，普通回拨会切换为忙音节奏。页面和日志记录完整调试信息，但电话里只播报整理后的结果。

## 页面方案

后台控制台：`http://127.0.0.1:8765/`

- 配置业务模式、语音密钥和硬件引脚。
- 查看 GPIO、UDP、录音、ASR/TTS、Agent 和回拨日志。
- 管理 Agent session、prompt、tools、skills 和对话记录。
- 手动测试蜂鸣器、LED、模拟摘机/挂机和 hook。

地球指挥中心：`http://127.0.0.1:8765/command-center/`

- 作为电话待命主屏。
- 展示阶段：等待命令、接收指令、执行命令、等待接听、播报结果。
- 接收 Agent 通过 `/events` 发来的地图命令。
- 使用 `window.AILandline` 桥接对象执行页面动作。

## 密钥方案

密钥只放在本机 `.env`：

```text
VOLCENGINE_API_KEY=   # ASR / TTS
ARK_API_KEY=          # 通讯员润色和 Agent 角色回复
```

ASR/TTS 和思考模型可以使用不同 key。后台页面也应该按这个分类维护，不把所有模型配置混在一起。

## 硬件基线

默认引脚：

```text
GPIO0  = 摘挂机输入
GPIO21 = 蜂鸣器
GPIO20 = LED
```

HG113 参考接法：

```text
GPIO0 -> HG113 六脚簧片开关 6 脚
GND   -> HG113 六脚簧片开关 2 脚
```

这只是样机记录，不是所有电话的通用保证。实际制作必须用万用表确认触点、线序、供电和绝缘。

## 推荐开发顺序

1. 先跑软件：`Start_AI_Desk_Phone.bat`。
2. 用模拟页验证摘机、挂机、回拨和 Agent 文字入口。
3. 配置 ASR/TTS 和 Ark key，验证电话回话。
4. 烧录 ESP32-C3，接 GPIO0，确认摘挂机状态。
5. 接蜂鸣器和 LED，确认回拨节奏。
6. 接听筒音频模块，确认 Windows 麦克风和扬声器可用。
7. 只在项目 `.pi/skills` 里扩展真实需要的本地技能。
