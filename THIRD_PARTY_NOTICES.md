# Third-Party Notices

AI Desk Phone 使用或运行时访问以下第三方代码、素材和服务。各项目名称和商标归其权利人所有。本文件只做归属说明，不改变任何上游许可证，也不代表第三方认可本项目。

项目自身代码许可证尚待项目所有者选择；第三方条款始终独立适用。

## 随仓库分发

### Three.js r160

- 文件：`web/variant-earth-command-center/vendor/three.module.js`
- 上游：[mrdoob/three.js](https://github.com/mrdoob/three.js)
- 许可证：MIT
- 随附许可证：[licenses/threejs-MIT.txt](licenses/threejs-MIT.txt)

### three-globe 示例纹理

- 文件：`web/variant-earth-command-center/assets/earth-water.png`
- 上游：[vasturiano/three-globe](https://github.com/vasturiano/three-globe)
- 许可证：MIT（上游仓库）
- 本仓库文件与 `three-globe@2.45.2` 示例资产的 SHA-256 一致。
- 随附许可证：[licenses/three-globe-MIT.txt](licenses/three-globe-MIT.txt)

### Solar System Scope 地球纹理

- 文件：`earth-day.jpg`、`earth-night.jpg`、`earth-specular-clouds.jpg`
- 来源：[Solar System Scope Textures](https://www.solarsystemscope.com/textures/)
- 许可证：[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- 修改：为 WebGL 使用调整为 4096×2048、压缩并重新命名；`earth-specular-clouds.jpg` 把镜面与云层信息打包在颜色通道中。
- 参考归属说明：[Three.js Journey Earth lesson](https://threejs-journey.com/lessons/earth-shaders)

### 硬件照片

- `docs/electronics/assets/photos/` 中的照片为项目样机记录，只用于说明可见硬件，不替代现场测量。

## 运行时依赖

### MapLibre GL JS 5.6.2

- 用途：指挥中心地图渲染。
- 上游：[maplibre/maplibre-gl-js](https://github.com/maplibre/maplibre-gl-js)
- 许可证：BSD 3-Clause。
- 当前从 unpkg CDN 加载，URL 固定到 `5.6.2` 并校验 SHA-384 SRI。

### Lucide 1.23.0

- 用途：控制台图标。
- 上游：[lucide-icons/lucide](https://github.com/lucide-icons/lucide)
- 许可证：ISC。
- 当前从 unpkg CDN 加载，URL 固定到 `1.23.0` 并校验 SHA-384 SRI。

### 地图与影像服务

指挥中心在运行时访问：

- [OpenFreeMap](https://www.openfreemap.org/) 地图样式与数据；
- [Esri World Imagery and Reference services](https://www.esri.com/)；
- [Sentinel-2 cloudless](https://s2maps.eu/)：processing © EOX，图层包含经修改的 Copernicus Sentinel 数据（2024）；
- [NASA GIBS](https://www.earthdata.nasa.gov/eosdis/science-system-description/eosdis-components/gibs) 影像服务。

页面保留可见署名。使用者还应遵守各服务的使用条款、归属要求、速率限制和数据许可。项目不缓存或再分发这些在线图层。

## Python 与固件依赖

Python 包和 PlatformIO/Arduino 库由 `requirements.txt` 与各 `platformio.ini` 声明。安装时会下载对应上游软件包；再分发二进制或发行包前，应生成依赖清单并复核每个许可证。

如果发现遗漏或错误归属，请按 [CONTRIBUTING.md](CONTRIBUTING.md) 提交更正；涉及潜在权利问题时请私下联系维护者。
