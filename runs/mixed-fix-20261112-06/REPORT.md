# 混合分辨率启动修复验收

修复前先取得 Pi 实际失败帧，见 [diagnosis.md](diagnosis.md)。Windows 原生1200p控制图经USB采集成1080p，完整矩形被非等比缩放；Pi输入时序查询不受支持，原decode拒绝矩形比例，握手停在round=0。

启动采集现在独立于本机发送尺寸：设备提供有效输入时序时优先使用；否则依次尝试声明支持的1080p/1200p，每4秒无有效控制即关闭并重开。识别有效控制后保持该采集模式。控制框定位后分别归一化横纵坐标，仍使用原RS、控制头和CRC校验。选档后的重开采集、标定、ModeSession和业务编解码接口均复用现有实现。

软件验证：Pi已有`.venv-hardware`运行当前仓库35项unittest全部通过，pip check通过。测试包括双尺寸原图、双方向缩放、真实Pi失败PNG、采集模式优先级及首次模式无有效控制时重开。见`tests.log`。Pi与Windows共享目录全部Python源码SHA一致，见`source-manifest.json`。Ubuntu原验证虚拟环境已不存在，因此软件测试在Pi执行。

硬件验收全部通过：每轮180秒独立业务时限，Windows源3,100,000字节、Pi源3,000,000字节，双方各至少3片。初始显示、初始实际采集、最终TX/RX、最终实际采集、结果状态、回执和源/接收SHA逐项从证据重新读取。

过程记录：`diagnosis-A-*`为修复前短连接；`A-*`为方向A。`B-*`、`B-final-*`是切换工具使用Windows CDS_FULLSCREEN临时标志导致退出即恢复的额外双端1200p轮次，不计入方向B。设置工具现使用普通桌面模式标志0；后续以`B-accepted-*`、`C-final-*`为准。原先对退出恢复时序的推测由实际标志行为纠正。所有尝试单独保存。

首次软件测试混入Pi残留的历史tests，见`tests-stale-deployment.log`；独立目录首次缺少测试按文件位置读取的configs，见`tests-isolated-missing-config.log`。补齐当前配置后完整35项通过；没有将历史测试失败记录作通过处理。

复现：使用本目录`hardware_check.py`（复用原自动协商验收脚本）和`set_windows_1080.py`；两端登录真实桌面、推荐设备选择Windows device=0 monitor=1 / Pi device=0 monitor=0、双向HDMI/USB接线。运行脚本依赖现有`/tmp/vl_remote.py`读取不提交的连接信息；凭据不在证据和Git中。

`B-verified`已完成双向交付且SHA一致，但Windows结果记录progress.tmp替换progress.json时WinError 5，故判失败。验收脚本原先在共享HGFS目录轮询Windows进度文件；重跑B-accepted停用运行中该文件读取（只读最终结果），避免验证过程干扰文件替换。不将回执或SHA通过替代result=complete。


| 场景 | 初始Windows / Pi | Windows TX / RX | Pi TX / RX | 双向结果与SHA |
|---|---|---|---|---|
| A | 1200p / 1080p | 1200p / 1080p | 1080p / 1200p | complete / 一致 |
| B（B-accepted） | 1080p / 1200p | 1080p / 1200p | 1200p / 1080p | complete / 一致 |
| C（C-final） | 1080p / 1080p | 1080p / 1080p | 1080p / 1080p | complete / 一致 |

六个方向的实际采集均与对端发送尺寸一致，标定symbol_errors=0；双方各至少3片，发送确认和接收FINISH回执均完成。每个源文件及接收文件完整SHA、初始显示和实际采集参数见[summary.json](summary.json)，独立断言见[verification.log](verification.log)。每端原始结果在`A-{windows,pi}/result.json`、`B-accepted-{windows,pi}/result.json`及`C-final-{windows,pi}/result.json`；各目录startup保存HELLO/CAPS/SELECT日志、READY及标定图/结果。

最终两端保持1080p，见`pi-final-capabilities.json`、`windows-set-1080.json`。实测使用V4L2 BGR3、DirectShow RGB24；Pi驱动报告30fps，Windows驱动未提供FPS。没有用软件测试代替硬件性能结论，也未做实机黑屏故障注入。完整采集/源/接收文件保留在本run目录；Git提交包含报告、摘要、哈希清单及回归夹具，不纳入大体积运行数据。
