# 依赖与发行清单

本文记录项目直接依赖、更新方式和发行前复核要求。版本范围以配置文件为准；安装得到的传递依赖会随解析时间变化。

## Python 直接依赖

| 包 | 声明范围 | 用途 | 上游许可证 |
| --- | --- | --- | --- |
| PlatformIO Core | `>=6.1,<7` | 固件构建、上传和设备管理 | Apache-2.0 |
| pySerial | `>=3.5` | USB 串口扫描与通信 | BSD-3-Clause |
| Requests | `>=2.31,<3` | HTTP 服务调用 | Apache-2.0 |
| websockets | `>=12,<16` | 流式 ASR WebSocket | BSD-3-Clause |
| sounddevice | `>=0.4.6,<0.6` | 本地录音和流式播放 | MIT |

许可证标识来自上游包元数据；实际发行必须连同解析后的确切版本和传递依赖再次核对，不能只依赖本表。

## 固件依赖

- 当前 ESP32-S3 主环境位于 `firmware/esp32c3_gpio0_21_test/platformio.ini`，使用 pioarduino 的 `platform-espressif32` 稳定发布地址和 Arduino framework。
- `stable` URL 是浮动引用，日常开发便于更新，但不可作为可复现发行依据。每次发行必须记录实际解析到的平台版本、包版本和工具链版本。
- 历史 ESP32-C3 BLE 诊断环境还声明 `NimBLE-Arduino` 与 `ArduinoJson`；它不属于当前 ESP32-S3 主固件发行基线。

## 浏览器代码、素材和在线服务

随仓库分发的 Three.js、纹理素材，CDN 加载的 MapLibre/Lucide，以及地图影像服务统一登记在 `THIRD_PARTY_NOTICES.md`。新增脚本、字体、图标、照片、模型或在线图层时，必须同时记录来源、版本、许可证、修改情况和必要署名。

## 更新策略

仓库不启用 Dependabot，也不让自动更新长期占用远端分支。维护者至少在准备发布时手动检查：

1. Python 直接依赖是否有兼容、安全或许可证变化；
2. CI 中 GitHub Actions 的主版本和提交来源；
3. pioarduino/PlatformIO 实际解析版本；
4. CDN 资源版本与 SRI 是否匹配；
5. 传递依赖和发行包是否引入新的通知或源码提供义务。

## 发行快照

在干净的发行环境中安装依赖后，至少保存以下命令输出到对应 GitHub Release 的构建附件或构建记录中：

```powershell
.\.venv\Scripts\python.exe -m pip freeze
.\.venv\Scripts\platformio.exe pkg list -d firmware\esp32c3_gpio0_21_test -e esp32s3_gpio0_21_test
```

如发布二进制包，推荐同时生成 CycloneDX 或 SPDX SBOM。SBOM 应包含传递依赖和实际版本，不能用本文件替代。
