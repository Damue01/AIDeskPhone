# 2026-07-08 工作区更新汇总

本报告汇总当前工作区相对 `origin/hg113-main` 的修改，覆盖 HG113 控制台、豆包 / 火山引擎语音链路、接线员回话队列，以及服务通信中枢地球页的真实卫星地图方案。

## 总览

- 控制台主线从单纯的 Codex hook 提醒，扩展为 `codex`、`doubao`、`voice_call` 三类业务模式的本地电话控制台。
- 新增回话队列：AI 任务完成后把文本加入队列，电话响铃提醒，摘机后播放回话，挂机时停止或重新进入提醒。
- 新增豆包 / 火山引擎语音能力：TTS 2.0 用于把回话生成音频，BigASR 用于把电话录音转成文字。
- 新增本地录音模块：支持麦克风录音、静音检测、WAV 落盘和识别结果回写。
- 服务通信中枢页合并 Three.js 漂亮地球与 MapLibre 真实卫星地图：默认展示风格化地球，放大后进入卫星地图，并保留城市标签提示。
- 真实数据策略收敛为“真实地图地球 + 云层暂不启用 + 城市级缩放”，不再追求实时卫星照片级地球，也不做照片云层去除。

## 主要文件

- `tools/ai_desk_phone_console.py`：扩展控制台状态机、回话队列、语音配置、录音接口、模拟页和前端控制面板。
- `tools/audio_recorder.py`：新增基于 `sounddevice` 的本地录音工具，输出 WAV 和录音元信息。
- `tools/volcengine_speech.py`：新增豆包 / 火山引擎 TTS 与 BigASR 封装，支持 `.env` 配置和兼容旧凭据模式。
- `config/ai_desk_phone_console.json`：默认切到 `doubao` 业务模式，并加入 TTS、ASR、录音、回话策略和 Agent 权限配置。
- `.env.example`：新增可提交的语音服务配置模板；真实 `.env` 继续忽略。
- `requirements.txt`：新增 `requests`、`websockets`、`sounddevice`。
- `.gitignore`：忽略 `.env`、`.env.*` 和 `data/`，保留 `.env.example`。
- `docs/HG113_PRODUCT_PLAN.md`：补充豆包聊天模式、Agent 通讯员模式、回话队列、语音接口和状态模型。
- `docs/CODEX_OPERATOR_HOOK.md`：补充模拟页、回话队列、豆包 TTS / ASR、语音 API 和 hook 请求体说明。
- `docs/HG113_COMMAND_CENTER_CONCEPTS.html`：新增服务通信中枢视觉概念稿。
- `web/variant-earth-command-center/index.html`：把 Three.js 地球和 MapLibre 卫星地图整合到主页面。
- `web/variant-earth-command-center/README.md`：更新页面定位、视觉原则和真实地图路线。
- `web/variant-earth-command-center/REAL_DATA_PLAN.md`：记录真实数据接入决策。
- `web/variant-earth-command-center/real-map.html`：保留真实地图页的探索版本，作为后续对照和备份。

## HG113 控制台

控制台现在把“电话摘挂机”当作上层业务入口，而不是只做硬件调试。新增配置项包括：

- `business_mode`：支持 `codex`、`doubao`、`voice_call`。
- `enable_callback`：是否启用任务完成后的电话回话提醒。
- `enable_tts_playback`、`tts_rate`、`tts_volume`、`audio_output_device`：控制本地回话播放。
- `enable_voice_asr`、`voice_record_sample_rate`、`voice_record_device`、`voice_auto_transcribe`、`voice_reply_policy`：控制摘机录音、自动识别和识别后回话策略。
- `agent_permission_profile`：为后续本地 Agent 权限边界预留。

新增或扩展的接口包括：

```text
GET  /api/replies
POST /api/replies
POST /api/replies/clear
POST /api/playback/stop

GET  /api/speech/status
POST /api/speech/config
POST /api/speech/transcribe-file

GET  /api/voice/status
POST /api/voice/start
POST /api/voice/stop
```

`POST /api/ai/hook` 和 `POST /hook` 继续保留，并会从 `text`、`reply`、`summary`、`message` 等字段提取回话文本。

## 语音链路

当前语音链路采用本地控制台负责音频和云服务调用的方式：

- 摘机后可启动本地录音，录音文件保存到 `data/recordings/`。
- 录音依赖 `sounddevice`，没有设备或依赖缺失时只记录错误，不影响控制台其他功能。
- ASR 使用豆包 / 火山引擎 BigASR WebSocket 接口。
- 回话播放优先尝试豆包 TTS 2.0；未配置或失败时回退到 Windows TTS / 模拟播放。
- `.env.example` 只保存字段名和默认端点；真实密钥放在本机 `.env`，不会上传到 GitHub。

## 地球页与真实地图

服务通信中枢主页面现在以 `index.html` 为主入口，不再把真实地图能力拆成必须跳转的第二套体验：

- Three.js 地球仍承担默认视觉，保留参考设计里的地表文字跳动和更干净的球体质感。
- MapLibre 承接放大后的真实地图能力。
- 城市地图使用 EOX Sentinel-2 cloudless 2024 卫星瓦片，避免街道图风格和主地球视觉割裂。
- Three.js 地球贴图也接入 EOX WMS 生成的卫星影像，尽量让地球态和地图态视觉接近。
- 卫星地图上增加城市标签层，放大后仍能知道当前区域。
- 继续保留 `real-map.html` 作为探索页，主路径以 `index.html` 的融合版本为准。

当前明确不做：

- 不做实时卫星照片级地球。
- 不要求去除卫星照片里的云。
- 不启用此前测试的 NASA GIBS 科学云量图层，因为红色 / 粉色数据配色不适合作为产品默认视觉。
- 不在地球边缘添加轨道、半透明圆环、散点星环等额外装饰。

## 验证与风险

已计划随本次提交执行：

```powershell
.\.venv\Scripts\python.exe -m py_compile tools\ai_desk_phone_console.py tools\audio_recorder.py tools\volcengine_speech.py
git diff --check
```

仍需人工体验确认：

- MapLibre 地图模式下的滚轮缩放和回到地球的手感，自动化浏览器之前无法稳定复现所有滚轮事件。
- EOX 公共瓦片服务的可用性、授权边界和生产环境缓存策略。
- 真实麦克风、蓝牙音频模块、豆包 / 火山引擎密钥配置后的端到端语音通话体验。

## 后续建议

1. 先把 HG113 控制台的语音链路跑通：录音、ASR、回话队列、TTS 播放。
2. 再调地球页缩放交互，重点修正“当前位置放大后落点偏差”和“地图态缩小时返回地球”的体验。
3. 如果继续推进真实地图，建议增加本地或服务端瓦片代理，统一处理缓存、失败兜底和 attribution。
4. 云层能力暂缓；只有拿到视觉上接近白色真实云图的来源，或有可靠的服务端调色方案后再恢复。
