# 项目上下文

统一Python HDMI/USB双向文件通信；网络仅用于开发管理。核心协议/RS/映射/存储位于core，平台设备位于backends，session负责协商，transport负责ACK/重传/发送自适应，bootstrap编排连接与后台编码/发布。

固定数据基线：1080p、16灰度、RS(255,247)、头RS(255,127)、列交织，满载1,000,103字节。接收方向/ROI/判决使用标定结果。协议字段见docs/PROTOCOL.md。

connect使用FileSource/DiskReassembler，磁盘逐片读写，后台整文件SHA/fsync后发布；先ACK分片，发布成功后FINISH。当前发布要求输出文件系统支持硬链接。负载缓存有界，但分片历史元数据仍随文件片数增长。

两个方向独立初始协商；发送端自适应速率/在途数量/RTO，接收w固定。公共窗口默认1，当前硬件推荐配置显式8。每会话每方向一个文件，不支持同会话配置切换。

Windows使用.venv-win，输出DISPLAY2保持1920×1080 identity，temporary_1080p=false；Pi使用.venv-hardware及真实桌面。Ubuntu验证环境/tmp/visual-link-venv。连接资料仅在根.local，禁止输出或提交。

当前结果见IMPLEMENTATION_STATUS.md；后续顺序见../IMPLEMENTATION_PLAN.md；历史证据见runs/README.md。不要将历史内存重组、固定RTO和旧显示恢复要求当作当前行为。

日常使用入口：[README双端启动与小文件示例](使用说明.md)。推荐 `configs/windows-recommended.json` / `configs/pi-recommended.json`，来源为throughput-rate-20261110-01的长测配置；控制缓存默认开启、控制停留0.10秒、窗口8、本机发送上限1.0/1.6FPS。Windows沿用 `scripts/windows_interactive.ps1 -Mode connect`，Pi用 `.venv-hardware/bin/python -m visual_link connect`；输入为任意本地文件，输出目录每次全新。双向预算按文件大小/两向有效速率取较大值，再留协商、发布与启动差余量。当前81项Ubuntu软件结果与历史硬件结果分别引用，归档修复尚未部署远端。
