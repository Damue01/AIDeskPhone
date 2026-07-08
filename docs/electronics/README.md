# 硬件资料索引

这个目录只保留 AI Desk Phone 开源项目需要的硬件资料。

## 内容

- [照片索引](photo-index.md)：说明每张原始照片里能看到的板卡、丝印、线束和可用信息。
- [原始照片](assets/photos/)：座机内部、CSR8645、ESP32-C3、稳压模块和接线参考照片。
- [HG113 连接手册](../HG113_CONNECTION_MANUAL.md)：记录当前已经跑通的 HG113 接线、Wi-Fi、网页控制台和排查步骤。
- [HG113 抽象连接参考图](assets/hg113_reference_wiring.svg)：按电气关系绘制的参考图，不依赖原始照片位置。

## 当前结论

- ESP32-C3 负责摘挂机识别、Wi-Fi 状态上报，以及执行蜂鸣器/LED 命令，不负责音频。
- CSR8645 负责蓝牙音频输入/输出。它需要 3.7V 供电，不要用 ESP32-C3 的 3.3V，也不要把 5V 直接接到 `BAT+`。
- RJ9/R9/4P4C 听筒线是四芯线，通常是一对喇叭线、一对麦克风线。最终线序以万用表、转接板标注和录音检查为准。
- 当前方案不接电话外线，避免振铃高压和未知线路状态。

HG113 当前接线以 [../HG113_CONNECTION_MANUAL.md](../HG113_CONNECTION_MANUAL.md) 为准；早期通用制作流程见 [../BUILD_MANUAL.md](../BUILD_MANUAL.md)。
