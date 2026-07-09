# AI Desk Phone 交互规范

这份文档是电话控制台的行为契约。实现、测试和调试日志都应该尽量对齐这里的流程。

## 电话体验

电话应该像一台本地 AI 服务终端：

1. 用户拿起听筒。
2. ASR 开始实时识别。
3. 用户说出任务。
4. 用户停顿或挂机后，这段语音提交成一轮 user turn。
5. Agent 在后台处理、调用工具、生成电话回话。
6. 如果用户已挂机，处理不能被取消；完成后蜂鸣器和 LED 提醒用户接听。
7. 用户再次拿起听筒后，TTS 播报结果，并可以继续下一轮对话。

内部日志可以记录工具、session、ASR chunk 和调试细节，但电话里播报给用户的内容必须短、稳、自然，不暴露 JSON、prompt、tool call 或 skill 名称。

## 输入法模式

输入法模式适合 Codex、编辑器、聊天框或其他负责执行任务的第三方应用。

1. 用户摘机。
2. 控制台触发配置好的摘机动作，通常是开始目标应用的语音输入。
3. 用户说话。
4. 用户挂机。
5. 控制台触发配置好的挂机动作，通常是结束输入、等待约一秒并按 Enter。
6. 第三方 AI 应用处理任务。
7. 第三方应用完成后调用本地 hook。
8. 控制台把 hook 文本整理成通讯员回话并加入回拨队列。配置了 `ARK_API_KEY` 时先让 Ark 角色模型润色；不可用时使用原文。
9. 电话响铃/亮灯。用户摘机后按队列播放回话。
10. 输入法模式下，如果用户在播报中挂机，本次播放停止。

## Agent 模式

Agent 模式适合让电话本身成为 AI 终端，不要求第三方应用在前台。

1. 用户摘机后进入本地 Agent 语音会话。
2. 控制台把麦克风音频按小块发送给 BigASR 流式识别。
3. 用户停顿、达到稳定文本、达到最长录音时间，或挂机后，这段语音提交。
4. Agent 可以先快速确认，也可以直接进入后台任务。
5. 挂机只关闭麦克风和听筒，不取消已提交的后台任务。
6. 任务完成后，控制台把结果加入回话队列，并进入 LED/蜂鸣器回拨提醒。
7. 用户摘机后，TTS 播报通讯员回话。
8. Agent 播报中挂机时，本次回话暂停并重新放回回拨流程；用户再摘机后继续播报。
9. 用户在提交前说出明确取消意图，例如“取消”“不用了”“撤回”，应取消本轮语音，不提交任务。

## 最小 Agent Runtime

Runtime 文件：

```text
tools/agent_runtime.py
```

一次用户轮次的内部流程：

1. 电话 ASR 或 `POST /api/agent/turn` 进入一条 user 消息。
2. 消息写入 append-only session JSONL。
3. Prompt builder 组装 `system`、`developer`、可用工具、可用 skills、已加载 skill 内容、压缩摘要和最近消息。
4. Agent 规划一个或多个显式 tool call。
5. 规划结果写入 assistant `toolCall` 消息。
6. 工具执行结果写入 `toolResult` 消息。
7. 电话 Agent 角色模型根据用户文本、工具结果和最近对话记录生成回话。
8. 回话写入 assistant 文本消息，并进入既有回话队列/回拨流程。
9. 当上下文变长时，较早消息会被压缩成结构化摘要，最近消息保持原文。

Session 文件保存在：

```text
data/agent_sessions/
```

后台页面应能维护这些内容：

- session id、文件路径、消息数量和最近消息。
- 新建 session。
- 删除 session。
- system prompt 和 developer prompt。
- 可用工具。
- 可用 skills 与本轮 loaded skills。
- 最近一次完整 prompt。
- 最近一次压缩摘要。

## Skill 加载边界

项目只加载本地 skills：

```text
<project>/.pi/skills/
```

默认本地 skill：

```text
.pi/skills/command-center-earth/SKILL.md
```

不要从全局 PI、用户目录 `.agents` 或外部插件目录加载 skills。本项目不需要那一大批全局能力；需要新能力时，在仓库内新增或修改 `.pi/skills`，让行为可审计、可提交、可复现。

Skill 遵循 progressive disclosure：prompt 默认只给名称和描述；当用户显式 `/skill:name` 或任务匹配时，再加载对应 `SKILL.md` 全文。

## 工具面

```text
set_phase       -> 切换指挥中心状态阶段
focus_city      -> 定位城市
fly_to          -> 跳转经纬度
show_globe      -> 返回地球首页/屏保
lookup_weather  -> 查询简短天气摘要
search_web      -> 打开浏览器搜索
open_url        -> 打开 URL
read            -> 读取项目内文件
grep            -> 搜索项目文本
find            -> 查找项目路径
ls              -> 列出项目目录
launch_app      -> 启动 allowlist 中的本地程序
run_command     -> 执行用户明确要求的命令，带危险模式拦截
```

文件工具只读项目目录。命令工具必须有明确用户意图、超时、危险命令拦截和输出截断。文件写入、多 Agent 协作、任意外部技能加载暂时不进入最小 runtime。

## 地球指挥中心事件

Agent 不直接操作浏览器 DOM，而是通过 `/events` 发布 `command_center_command`：

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

页面收到事件后调用自己的 `window.AILandline` 桥接方法。这样地球渲染仍然属于页面，Agent 只表达意图。

## 文本入口

```http
POST http://127.0.0.1:8765/api/agent/turn
Content-Type: application/json

{
  "source": "codex",
  "text": "定位北京",
  "reply_behavior": "direct"
}
```

## 角色和回话

电话里的角色名叫“小叶”，默认称呼用户为“首长”。回话原则：

1. 简短确认做了什么。
2. 不说自己是模型、系统、工具或 skill。
3. 不复述内部 JSON、日志或 prompt。
4. 工具失败时如实报告失败原因，不编造已经完成。
5. 地图操作成功时示例：“首长，已定位上海。”“首长，已回到地球首页。”

## ASR 词汇

项目名、产品名、Codex、HG113、地名和用户自定义词应尽量放入语音服务的热词/boosting table。`.env.example` 支持：

```text
DOUBAO_ASR_HOTWORDS=键斗,Codex,HG113
DOUBAO_ASR_BOOSTING_TABLE_ID=
DOUBAO_ASR_BOOSTING_TABLE_NAME=
```
