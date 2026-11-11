# 当前实施状态

更新：2026-11-11。后续任务以[实施计划](../IMPLEMENTATION_PLAN.md)为准。

最初五阶段基础链路已完成；当前已有双向视频发现、标定、初始协商、ACK/选择性重传、发送端自适应和磁盘文件重组。

历史1FPS验收：两向各1,850,190,673字节，1851片，约31.4分钟持续新数据，本机1FPS/窗口8；各1次重传，完整交付通过。三端66项测试为历史验收结果。本次Ubuntu回归见[审查日志](../maintenance/review-20261110/tests.log)。

证据：[最终报告](runs/storage-live-20261110-02/REPORT.md)、[结构化结果](runs/storage-live-20261110-02/verified.json)。首轮FINISH超时失败见storage-live-20261110-01，不计成功。Pi进程无IP网络而宿主SSH在线；大二进制部分保留远端，核验口径为全文件/分块SHA。

尚未完成：动态接收额度、多设备/档位实测、同会话配置切换。当前每会话每方向一个文件。

已完成接收队列等统计和默认开启的连续相同控制图像单帧解码缓存；上一轮五轮成功/一次预算不足失败及Ubuntu72项历史验收见[原报告](runs/throughput-live-20261110-01/REPORT.md)。

缓存配对及0.10秒控制停留三轮低速验证见[上轮报告](runs/throughput-paired-20261110-01/REPORT.md)。本轮只提高本机上限至Windows→Pi 1.0FPS / Pi→Windows 1.6FPS，保持缓存、窗口8及显示配置，三轮60秒短测与6次完整交付SHA通过，两向交付吞吐0.821–0.867 / 1.403–1.435MB/s；Windows短测受0.882–0.962FPS协商值约束，Pi有一次重传后恢复。

随后同上限更高速长测完成：两向分别1,900,195,823 / 3,000,309,123字节，持续新数据34.4/35.7分钟，整文件交付0.888/1.394MB/s，重传0/1，数据队列零丢弃（Pi控制队列2次丢弃）；独立重读完整SHA一致、双向FINISH通过。已验证本轮非对称大小及Pi→Windows超过2GiB实际交付。Pi发布约74秒期间仍发送剩余业务，部分60秒窗口下降，详见报告，不仅报告首个窗口。两端进程/显示任务已回收，DISPLAY2保持1080p identity。

采用缓存开启、控制停留0.10秒、窗口8、本机上限1.0/1.6FPS作为本硬件推荐工作点，保留全局默认0.25秒。P1本工作点三轮短测及一次30分钟持续新数据验收完成，不称设备峰值；运行代码未改，参数实验复用上轮Ubuntu73项回归。本轮共8次完整交付，证据见[速率与长测报告](runs/throughput-rate-20261110-01/REPORT.md)、[短测核验](runs/throughput-rate-20261110-01/short-verified.json)、[长测核验](runs/throughput-rate-20261110-01/verified-long.json)。

P2限定补测已完成：新增8项软件注入覆盖发布期间对端身份重启、写入错误/短写、fsync/硬链接发布失败及输出/归档不覆盖；真实采集子进程、发布线程和文件描述符回收通过。发现并修复重启归档rename在Ubuntu可覆盖同名历史文件的问题，改用不覆盖的硬链接归档。Ubuntu完整81项回归通过（21.494秒），没有重跑Windows/Pi或硬件长测；现有Ubuntu环境无pip。详见[可靠性补测报告](runs/reliability-software-20261110-01/REPORT.md)。测试覆盖可返回错误和可完成的慢发布，不承诺中断永久阻塞的内核I/O。

P1本工作点和P2有限验收完成。当前无数据队列积压证据，不启动动态额度；无新设备需求，不启动新档位。后续依真实需求进入P3/P4，磁盘重组、历史1FPS长测及本工作点30分钟验收不重复列为待办。P2归档源码修复仅在本地验证，未部署远端；远端保留此前实机验收版本。

历次实现及失败过程见[runs索引](runs/README.md)。整理前的完整状态文本保存在`../maintenance/review-20261110/documents-before.tar.gz`。

本地存储已清理846个冗余/可重建文件，约14.10GB；大文件完整字节仍有Pi/本地副本，报告、统计、失败日志、源码和运行环境保留。原路径恢复见[清理说明](../maintenance/cleanup-20261110-redundant/README.md)。旧HANDOFF/WORK_PROGRESS入口已移除；清理时73项回归见该目录tests-ubuntu.log；后续最新81项结果见reliability-software-20261110-01/tests-ubuntu-final.log。

日常使用入口已交付：[Windows推荐配置](configs/windows-recommended.json)、[Pi推荐配置](configs/pi-recommended.json)，参数源于rate长测，去除实验字段，缓存沿用默认开启。两端任意文件发送命令、10/20MB小文件示例、预算与完成状态见[README](使用说明.md)。Windows沿用 `scripts/windows_interactive.ps1 -Mode connect`，Pi沿用 `.venv-hardware/bin/python -m visual_link connect`。本轮配置加载、长测参数一致性、README双端命令的CLI解析/配置分派及本地文档链接检查通过（硬件入口替换为软件桩；Windows脚本参数按现有脚本核对，未执行PowerShell）。仅改配置和说明，复用上述81项软件回归与既有长测；未部署或重跑硬件。后续功能由实际需求确定。

2026-11-11项目整理：普通用户入口为使用说明.md，README保留安装及开发索引，部署脚本已包含中文说明。程序、配置、测试与文档纳入visual-link本地Git；实验数据及现用环境单独保留。
