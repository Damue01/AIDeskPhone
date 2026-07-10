# AI Desk Phone 文档中心

本目录只维护当前 `hg113-main` 的可执行事实。历史调试记录、早期产品构想和已废弃接线不作为用户文档保留。

## 权威文档

| 文档 | 适用对象 | 内容 |
| --- | --- | --- |
| [标准参考手册](REFERENCE_MANUAL.md) | 使用者、制作者、维护者 | 从模拟器到实机的完整安装、接线、固件、配置、运行、验证和排障 |
| [API 参考](API_REFERENCE.md) | 集成开发者 | HTTP、SSE、ESP32 命令协议和指挥中心 JavaScript Bridge |
| [硬件资料索引](electronics/README.md) | 硬件制作者 | 当前接线图、样机照片、历史板卡和现场证据 |
| [照片索引](electronics/photo-index.md) | 硬件制作者 | 每张样机照片可确认的信息与使用限制 |

组件级开发资料：

- [地球指挥中心](../web/variant-earth-command-center/README.md)
- [项目内地球 skill](../.pi/skills/command-center-earth/SKILL.md)

仓库协作与安全资料：

- [贡献指南](../CONTRIBUTING.md)
- [安全策略](../SECURITY.md)
- [威胁模型](../THREAT_MODEL.md)
- [行为准则](../CODE_OF_CONDUCT.md)
- [第三方声明](../THIRD_PARTY_NOTICES.md)

## 文档维护规则

1. 一项事实只保留一个权威来源。README 只做入口，不复制整段手册。
2. 引脚、端口、环境变量、命令和 API 必须以源码、脚本或测试为准。
3. 操作步骤必须同时给出前置条件、成功判据和失败恢复方式。
4. 使用固定状态标签：**当前支持**、**实验性**、**历史兼容**、**未支持**。
5. 示例不得包含真实密钥、Wi-Fi 凭据、局域网地址、用户名或未脱敏日志。
6. 行为改动必须在同一个 PR 中更新对应文档和测试。
7. 规划内容不写进参考手册；需要记录时放到 Issue 或 Discussion。

