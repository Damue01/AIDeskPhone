# Contributing to AI Desk Phone

感谢你改进 AI Desk Phone。项目同时涉及 Windows、Python、ESP32-S3、音频、网页和低压硬件，提交应保持范围清晰，并提供与风险相称的验证证据。

## 开始之前

- 阅读[标准参考手册](docs/REFERENCE_MANUAL.md)和[威胁模型](THREAT_MODEL.md)。
- Bug 和新功能先使用对应的 Issue 模板，说明问题发生在模拟器、桌面端、固件还是实机。
- 安全漏洞不要提交公开 Issue，按 [SECURITY.md](SECURITY.md) 私下报告。
- 不要提交 API Key、Wi-Fi 凭据、用户名、局域网地址、录音、会话内容或未脱敏日志。

## 开发环境

```powershell
git clone https://github.com/Damue01/AIDeskPhone.git
Set-Location AIDeskPhone
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

只有调试 ASR、TTS 或 Ark 时才需要在 `.env` 中填写真实密钥。

## 本地验证

### Python 和文档

```powershell
.\.venv\Scripts\python.exe -m unittest discover tests -v
```

### 当前 ESP32-S3 固件

```powershell
.\.venv\Scripts\platformio.exe run `
  -d firmware\esp32c3_gpio0_21_test `
  -e esp32s3_gpio0_21_test
```

### 页面

```powershell
.\Start_AI_Desk_Phone.bat simulator
```

至少验证配置页、模拟器和 `/command-center/` 能正常打开。UI 改动请在 PR 中附截图。

## 变更原则

### Python

- 优先保持标准库和现有依赖，新增依赖必须解释用途、许可证和维护成本。
- 硬件、文件和网络操作应有超时、错误处理和可测试边界。
- 新增公共行为时补回归测试。
- 不扩大 Agent 命令权限；涉及权限、shell 或网络绑定的改动必须更新威胁模型。

### 固件和硬件

- 当前基线是 ESP32-S3、GPIO4/2/1；历史目录名不能作为板型依据。
- 构建命令必须显式包含 `-e esp32s3_gpio0_21_test`。
- 接线改动必须说明电压、电流、共地、限流/驱动和电话外线隔离。
- 没有实机测试时明确标记“仅编译验证”，不要写成“已验证”。
- 不提交真实 Wi-Fi 凭据或 `wifi_credentials.h`。

### 文档

- README 是项目入口；完整事实写入 `docs/REFERENCE_MANUAL.md` 或 `docs/API_REFERENCE.md`。
- 一项引脚、端口、命令或环境变量只维护一个权威说明。
- 命令必须可复制，并使用 `COM7`、`YOUR_API_KEY` 等明显占位值。
- 删除功能时同步删除文档；不要把历史调试记录重新放入用户手册。
- 图片必须有替代文本、来源和许可信息。

## Pull Request 检查表

PR 描述至少回答：

1. 改了什么，为什么？
2. 是否改变用户行为、配置、协议或安全边界？
3. 执行了哪些测试？
4. 是否更新了文档和 `CHANGELOG.md`？

硬件相关 PR 请填写：

| 验证项 | 结果 |
| --- | --- |
| 模拟器 | 未测 / 通过 / 不适用 |
| Python 测试 | 未测 / 通过 |
| ESP32-S3 编译 | 未测 / 通过 |
| ESP32-S3 实机 | 未测 / 通过 |
| 摘挂机 | 未测 / 通过 |
| LED / 蜂鸣器 | 未测 / 通过 |
| Wi-Fi / TCP / UDP | 未测 / 通过 |
| 音频 / ASR / TTS | 未测 / 通过 / 不适用 |

保持 PR 小而聚焦。重构、行为变更和大规模格式化应尽量拆开，方便审查和回滚。

## 提交信息

推荐使用清晰的祈使句，或 Conventional Commits 风格：

```text
docs: consolidate the hardware manual
fix: keep web and device command ports separate
test: cover Codex notify argument payloads
```

## 行为准则

参与项目即表示同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

