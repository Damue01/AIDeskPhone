# 地球指挥中心

这是 AI Desk Phone 的本地状态主屏，由 Python 控制台托管：

```text
http://127.0.0.1:8765/command-center/
```

页面负责展示地球、地图和电话工作阶段；录音、ASR、Agent、TTS、回拨与硬件控制仍由 `tools/ai_desk_phone_console.py` 处理。

## 文件

| 路径 | 用途 |
| --- | --- |
| `index.html` | 页面、地球渲染、地图和 JavaScript Bridge |
| `assets/` | 本地降级地球纹理 |
| `vendor/three.module.js` | Three.js r160 本地副本 |

完整 Bridge、事件和 URL 参数见[项目 API 参考](../../docs/API_REFERENCE.md#6-指挥中心-javascript-bridge)。

## 运行方式

随主项目运行：

```powershell
.\Start_AI_Desk_Phone.bat simulator
```

只预览静态页面：

```powershell
python -m http.server 8790 --bind 127.0.0.1 --directory web\variant-earth-command-center
```

静态预览不能连接电话、ASR、TTS、Agent session 或硬件命令。

## 联网依赖

页面运行时会访问带版本锁定和 SRI 校验的 MapLibre CDN，以及 OpenFreeMap、Esri、EOX 和 NASA GIBS。CDN 或网络不可用时，页面会跳过在线地图并保留本地 Three.js 地球；真实地图和实时影像不可用。来源与许可见 [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md)。

页面必须保留可见的数据来源署名。修改地图样式或纹理时，需要在同一个 PR 中更新第三方声明。
