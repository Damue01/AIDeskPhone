# Changelog

本项目遵循“面向用户的变更记录”原则。版本号和发布流程见 `GOVERNANCE.md`；首个稳定版本尚未发布，当前改动先记录在 `Unreleased`。

## Unreleased

### Added

- 统一的标准参考手册、API 参考和文档中心。
- GitHub CI、Issue/PR 模板、贡献指南、安全策略、威胁模型和行为准则。
- 启动脚本的显式无硬件模拟模式。
- Codex notify 对官方 JSON 命令行参数的支持。
- 跨站 API 请求与非 JSON POST 防护、第三方 MIT 许可副本和 CDN SRI 校验。
- 项目 MIT 许可证、分支与发布规则、隐私和数据保留说明。
- ESP32-S3 参考物料清单以及依赖和发行 SBOM 检查要求。

### Changed

- 当前硬件基线统一为 ESP32-S3、GPIO4/2/1。
- HTTP 启动默认绑定 `127.0.0.1`。
- 默认 Agent 权限改为 `confirm_sensitive`。
- 网页固件烧录显式选择 ESP32-S3 环境。
- 实机 SOP 使用不同的 HTTP 8765 与 TCP 命令 8768 端口。
- 指挥中心恢复地图和影像来源署名。
- 一键模拟模式在运行时隔离串口、ESP32 网络、固件烧录和 Windows 快捷键。
- Agent session 删除增加 ID 并发保护，`silent` 成为静默回复的明确别名。

### Removed

- Dependabot 自动更新配置；依赖改为发布前手动复核，避免长期生成自动更新分支。
- 重复、过时的 ESP32-C3 接线/制作/产品规划文档。
- 已失效的 Codex repo-local Hook 说明。
- 孤立的视觉概念页、旧地图探索页和未使用地球纹理。
- 来源或再分发权无法验证的云层纹理与外部卖家接线图。
