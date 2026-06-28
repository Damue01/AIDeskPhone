# AI Desk Phone 制作与维护手册

- 整理日期：2026-06-28
- 固件版本：`hybrid-ble-config-v1`
- 默认串口：`COM3`
- 默认控制台地址：`http://127.0.0.1:8765`

这份文档是开源仓库中对外保留的主手册，整理了项目目标、仓库结构、配件外观、硬件注意事项、常见问题和发布前检查。开发过程中的临时脚本、重复排查记录和测试目录不再保留。

调试入口只有本地网页控制台。串口日志、板子状态、ADC 曲线、动作日志、配置保存和模拟按下/释放都在控制台里完成，不需要再运行其他辅助脚本。

## 1. 项目目标

AI Desk Phone 的目标是把老式座机改造成 Windows 语音通话控制器。

控制链路：

```text
电话摘挂机/按压机构
  -> ESP32-C3 GPIO1 / ADC
  -> 固件状态机判断按下/释放
  -> 名为 AIDeskPhoneKB 的 BLE HID 键盘
  -> Windows 语音通话软件快捷键
```

音频链路：

```text
电话听筒麦克风和喇叭
  -> RJ9/R9/4P4C 四芯听筒线
  -> CSR8645 蓝牙音频模块 MIC / SPK 焊盘
  -> Windows 蓝牙耳机输入/输出
```

控制链路和音频链路要分开调试。ESP32-C3 只负责摘挂机/按压识别和 BLE HID 快捷键；CSR8645 只负责蓝牙音频。

## 2. 当前完成内容

- ESP32-C3 固件读取 GPIO1/ADC，并判断 `PRESSED` / `RELEASED`。
- 固件作为 `AIDeskPhoneKB` BLE HID 键盘发送快捷键。
- 本地网页控制台显示 ADC 曲线、串口日志、板子事件和动作日志。
- 控制台可以保存阈值和动作配置到本地 JSON，并通过 USB 串口写入 ESP32 NVS。
- 控制台支持手动模拟按下/释放，用于验证 BLE HID 动作链路。
- 仓库保留了硬件照片、照片索引和 RJ9/R9/4P4C 接线注意事项。

仍需现场确认：

- RJ9/R9/4P4C 四芯听筒线中哪一对是喇叭、哪一对是麦克风。
- CSR8645 的 MIC 偏置是否正常，以及 Windows 录音输入是否能收到听筒麦克风声音。
- 最终装机固定、绝缘和应力释放。

## 3. 仓库结构

```text
README.md                         项目入口和快速开始
requirements.txt                  Python 依赖
config/ai_desk_phone_console.json 控制台默认配置
firmware/esp32c3_ble_gpio/        ESP32-C3 PlatformIO 固件
firmware/esp32c3_ble_gpio/src/    固件源码和 HID 报告定义
tools/ai_desk_phone_console.py    本地网页控制台和串口桥
docs/BUILD_MANUAL.md              本文档
docs/electronics/                 硬件照片和照片索引
```

不要提交本地生成内容，例如 `.venv/`、`.pio/`、`__pycache__/`、`logs/`、编辑器目录和临时文件。

## 4. 配件和外观对照

| 配件 | 长什么样 | 本项目用途 | 资料位置 |
| --- | --- | --- | --- |
| 富桥 HCD28(3)P/TSD 座机 | 老式桌面座机，内部有黄色主板、绿色前面板、摘挂机小板和四芯听筒线 | 提供外壳、听筒、摘挂机结构和物理交互 | [照片索引](electronics/photo-index.md)，`IMG_8607..JPG` 到 `IMG_8611..JPG` |
| ESP32-C3 SuperMini | 小型 USB-C 开发板，侧边有 `5V`、`G`、`3V` 等丝印，有 BOOT/RST 按键 | 读取摘挂机/按压状态，发送 BLE HID 快捷键 | `docs/electronics/assets/photos/IMG_8614..JPG` |
| CSR8645 蓝牙音频模块 | 窄长小板，约 35.5 x 8.3 mm，一面有 Micro USB、芯片、天线区，另一面有按键和焊盘 | 把听筒麦克风/喇叭变成 Windows 蓝牙耳机类输入/输出 | `IMG_8612..JPG`、`IMG_8613..JPG`、`IMG_8616..JPG` |
| RJ9/R9/4P4C 听筒线 | 电话听筒常见四芯线，现场可能口头叫 R9/RJ9，常见颜色为红、黑、黄、绿 | 四根线拆成两对：一对喇叭，一对麦克风 | 以转接板标注、万用表通断和录音/播放检查为准 |
| 备用稳压/电源模块 | 小电源板，端子常见丝印为 `VIN+`、`GND`、`VOUT+`、`GND` | 仅作为备用电源资料；CSR8645 使用 3.7V 供电 | `IMG_8615..JPG` |
| 万用表、杜邦线、夹线、热缩管 | 现场调线和绝缘工具 | 测电压、电阻、通断；临时接线；最终固定和绝缘 | 必备工具 |

## 5. 硬件注意事项

### 5.1 CSR8645 供电

CSR8645 蓝牙音频模块需要 3.7V 供电。不要用 ESP32-C3 的 3.3V 给它供电，也不要把 5V 直接接到 `BAT+`。

供电规则：

1. `BAT+ / BAT-` 输入使用 3.7V。
2. ESP32-C3 的 3.3V 不给 CSR8645 供电。
3. 5V 不接 CSR8645 的 `BAT+`。
4. 接听筒麦克风前先测：

```text
BAT+ 对 BAT- = 3.7V
MIC+ 对 BAT- = ? V
MIC- 对 BAT- = ? V
```

模块发热、重启、无灯、Windows 设备消失时，先断电。

### 5.2 ESP32-C3 和 CSR8645 是两个子系统

ESP32-C3 是控制子系统，CSR8645 是音频子系统。它们不需要因为装在同一个电话壳里就强行共用供电。

只有当两个子系统之间真的有信号互相读取时，才考虑共地；共地前必须确认电平不会超过 ESP32-C3 GPIO 允许范围。

### 5.3 RJ9/R9/4P4C 听筒线检查

听筒线是四芯线。接到转接板或端子后，通常就是：

```text
两根线 = 听筒喇叭 / 受话器
两根线 = 听筒麦克风 / 送话器
```

如果分不清线序，可以把 RJ9/R9/4P4C 插到转接板，或把四根线接到端子上，再只在低压音频脚之间试接：

- 喇叭候选线只接 CSR8645 `L+ / L-` 或 `R+ / R-`。
- 麦克风候选线只接 CSR8645 `MIC+ / MIC-`。
- 如果 MIC 正负不确定，可以交换 `MIC+ / MIC-` 再做一次录音检查。
- 不要把未知听筒线接到 `BAT+`、`5V`、`3V3` 或未知电源焊盘。
- 不要接电话外线。

有些参考会说“绿/黄是麦克风、红/黑是喇叭”，这个只能作为尝试顺序，不能当作通用标准。最终以转接板标注、万用表通断和录音/播放检查为准。

### 5.4 电话外线不接入

当前方案不使用电话外线。PSTN 外线可能有振铃高压和未知线路状态，开发时保持断开。

## 6. 固件

固件文件：

```text
firmware/esp32c3_ble_gpio/platformio.ini
firmware/esp32c3_ble_gpio/src/main.cpp
firmware/esp32c3_ble_gpio/src/hid_keyboard_reports.h
```

关键设置：

```text
输入脚：GPIO1 / A1
BLE HID 名称：AIDeskPhoneKB
默认采样间隔：50 ms
默认串口波特率：115200
默认按下动作：ctrl+win+shift
默认释放动作：ctrl+win+shift, delay:1000, enter
```

编译：

```powershell
.\.venv\Scripts\platformio.exe run -d firmware\esp32c3_ble_gpio
```

烧录：

```powershell
.\.venv\Scripts\platformio.exe run -d firmware\esp32c3_ble_gpio -t upload
```

PlatformIO 环境：

```text
esp32c3_supermini
esp32c3_supermini_jtag
```

## 7. 本地控制台

启动：

```powershell
.\.venv\Scripts\python.exe tools\ai_desk_phone_console.py --port COM3 --web-port 8765
```

打开：

```text
http://127.0.0.1:8765
```

只预览页面、不打开串口：

```powershell
.\.venv\Scripts\python.exe tools\ai_desk_phone_console.py --no-serial --web-port 8765
```

控制台功能：

- 显示串口日志、板子事件、动作日志和 ADC 曲线。
- 调整阈值、消抖、锁定时间、分数参数和采样间隔。
- 配置按下/释放动作快捷键。
- 保存配置到 `config/ai_desk_phone_console.json`。
- 通过 USB 串口把配置写入 ESP32 NVS。
- 手动模拟按下/释放，用于验证 BLE HID 动作链路。

说明：命令中的 `.venv\Scripts\python.exe` 是 Windows Python 虚拟环境目录，不是项目额外提供的调试脚本。

内置动作方案：

| 方案 | 按下 | 释放 | 用途 |
| --- | --- | --- | --- |
| 当前配置 | Ctrl + Windows + Shift，延迟 1000 ms，Enter | Ctrl + Windows + Shift | 保留现有流程 |
| 语音通话键 | Ctrl + Alt + I | Ctrl + Alt + U | 语音通话快捷键验证 |

浏览器和 Windows 可能会拦截 Windows 键。包含 Windows 键的组合建议直接用内置方案，不要强求在浏览器里录入。

## 8. 常见问题

### 8.1 控制台能打开，但找不到 ESP32 串口

常见原因：

- ESP32-C3 没有通过 USB 连接。
- 板子在其他 COM 口。
- 串口被其他程序占用。
- 当前 Python 环境没有安装 `pyserial`。

查看串口：

```powershell
Get-CimInstance Win32_SerialPort | Select DeviceID,Name,PNPDeviceID
```

用实际串口手动启动：

```powershell
.\.venv\Scripts\python.exe tools\ai_desk_phone_console.py --port COM3 --web-port 8765
```

### 8.2 `.venv` 指向失效的 Python

如果 `.venv` 报 `Unable to create process using Python312`，重建虚拟环境：

```powershell
Remove-Item -Recurse -Force .venv
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果没有 Python 3.12，可以用 `py -3.11`。

### 8.3 配置只保存到电脑，没有写进板子

控制台会先保存本地 JSON。写入板子需要 USB 串口连接正常。

看到下面日志才说明 ESP32 NVS 写入完成：

```text
ESP32 已确认配置写入板子。
```

如果没有这行，检查 COM 口，确认没有其他串口监视器占用。

### 8.4 断开 USB 后收不到日志

这是当前设计限制。实时日志走 USB 串口；配置写入 NVS 后，ESP32 可以继续执行 BLE HID 动作，但电脑无法在断开 USB 后接收串口日志。

如果以后需要无线日志，需要另做 BLE UART、Wi-Fi WebSocket/MQTT 或本地日志缓存。

### 8.5 CSR8645 供电要求

CSR8645 使用 3.7V 供电。不要用 ESP32-C3 的 3.3V 供电，不要把 5V 直接接到 `BAT+`。出现不开机、配对异常、麦克风无输入、设备反复断开时，先按 5.1 检查供电。

### 8.6 Windows 能看到蓝牙音频，但麦克风没声音

曾观察到 Windows 显示 `Connected mic, audio`，输入设备为 Headset，格式类似 1 channel / 16 bit / 16000 Hz，但录音没有明显输入。

排查顺序：

1. 确认 Windows 正在使用 CSR8645 的 Headset 输入，而不是其他麦克风。
2. CSR8645 单独上电并配对，先不要接听筒线。
3. 黑表笔固定 `BAT-`，测 `BAT+`、`MIC+`、`MIC-`。
4. 如果 `MIC+` 长期接近 0V，先检查 CSR8645 麦克风焊盘、偏置、焊点和工作模式，不要继续盲目换听筒线。
5. 断电后检查 `MIC+` 和 `MIC-` 是否短路。

### 8.7 RJ9/R9/4P4C 四芯线不好分

从原电话主板侧测到的阻值可能经过了原机电路，不一定是听筒喇叭/麦克风本体阻值。之前曾出现过 1 kOhm、20 kOhm 到 25 kOhm、小电压偏置等读数，所以不要只凭主板侧读数永久焊接。

更推荐在 RJ9 插头、转接板或听筒内部测量。仍然分不清时，只按 5.3 的规则在 CSR8645 音频脚上临时试接。

### 8.8 Windows 键录不进去

浏览器或系统可能拦截 `Win` 组合键。使用内置方案即可。

### 8.9 BLE 已连接但快捷键没反应

按顺序检查：

1. Windows 蓝牙里 `AIDeskPhoneKB` 是否已连接。
2. 控制台顶部串口状态是否已连接。
3. 固件日志里是否有 BLE connected 事件。
4. 控制台里动作执行是否开启。
5. 点击“模拟按下/模拟释放”是否产生日志。
6. 必要时删除并重新配对 `AIDeskPhoneKB`。

### 8.10 没有完整原机原理图

目前没有找到这台捐赠电话的完整厂家原理图。原电话主板、摘挂机小板和前面板都必须以实测为准，网上资料只能辅助判断方向。

## 9. 推荐调试流程

1. 电话外线保持断开。
2. ESP32-C3 通过 USB-C 连接电脑。
3. 编译并烧录固件。
4. 用实际 COM 口启动本地控制台。
5. 确认控制台显示服务已连接、串口已连接。
6. 操作电话摘挂机/按压机构，观察 ADC 曲线。
7. 调整阈值并保存配置。
8. 等待 ESP32 配置保存确认日志。
9. Windows 配对 `AIDeskPhoneKB`。
10. 用控制台模拟按下/释放验证快捷键。
11. 再用真实电话动作验证。
12. CSR8645 供电、MIC 偏置和 RJ9/R9/4P4C 线序单独排查，不要和 ESP32 控制链路混在一起。

## 10. 发布前检查

运行：

```powershell
.\.venv\Scripts\python.exe -m py_compile tools\ai_desk_phone_console.py
.\.venv\Scripts\platformio.exe run -d firmware\esp32c3_ble_gpio
```

可选控制台检查：

```powershell
.\.venv\Scripts\python.exe tools\ai_desk_phone_console.py --no-serial --web-port 8765
```

打开 `http://127.0.0.1:8765`，确认页面能加载、配置能编辑、模拟按下/释放有响应。

## 11. 相关资料

- [硬件资料索引](electronics/README.md)
- [照片索引](electronics/photo-index.md)
