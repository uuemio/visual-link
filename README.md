# Visual Link

Windows与树莓派通过HDMI/USB双向传输文件。

**开始使用：[中文使用说明](使用说明.md)**

## 项目结构

- `src/visual_link/`：通信程序。
- `configs/`：Windows和Pi推荐配置。
- `scripts/`：启动、部署和结果回收。
- `tests/`：软件测试。
- `docs/`：协议和设备技术说明。
- `runs/`：本机实验资料与运行输出。

## 安装

Ubuntu/树莓派（64位系统；若没有对应ARM wheel须另行选版并重测）：
```bash
python3 -m venv --copies .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip check
.venv/bin/python -m visual_link tx --config configs/offline.json --seed 20261105 --size 2000207 --output runs/demo/frames
.venv/bin/python -m visual_link rx --config configs/offline.json --input runs/demo/frames --output runs/demo/recovered.bin
```
Windows原生PowerShell，在共享文件夹的visual-link目录：
```powershell
py -3 -m venv --copies .venv-win
.venv-win\Scripts\python -m pip install -e .
.venv-win\Scripts\python -m unittest discover -s tests -v
.venv-win\Scripts\python -m pip check
.venv-win\Scripts\python -m visual_link tx --config configs/offline.json --seed 20261105 --size 2000207 --output runs/win-demo/frames
.venv-win\Scripts\python -m visual_link rx --config configs/offline.json --input runs/win-demo/frames --output runs/win-demo/recovered.bin
```
文件TX用`--input 文件`替代`--seed/--size`。每次用新输出路径，默认拒绝覆盖。RX默认读取原始发送图像；模拟采集图像使用`--captured`；不得混用ROI。
平台配置可用于 `display`（Linux GTK / Windows Tkinter）、`capture`、`rx-one`；`pattern/compare` 用于标定。完整安装、接线、运行和故障说明见[单帧硬件指南](docs/HARDWARE.md)。安装仍使用headless OpenCV，GTK从系统单独安装。部署与主机指纹步骤见[部署说明](docs/DEPLOYMENT.md)。
共享目录不支持符号链接，因此Linux建venv使用--copies。缺少ensurepip时先安装系统python3-venv；也可由已有pip通过`--python 新环境/bin/python`安装。
本工作区测试会与旁边保留的历史源码交叉比较旧编码；独立部署时用固定黄金SHA校验。源数据不会由RX读取。



## 开发与进度

- [当前状态](IMPLEMENTATION_STATUS.md)
- [架构与环境](PROJECT_CONTEXT.md)
- [协议](docs/PROTOCOL.md)
- [实验索引](runs/README.md)

最新Ubuntu回归81项通过。当前推荐配置完成双向超过30分钟持续传输，文件交付速度约0.888/1.394MB/s。归档修复已在本地测试，远端部署状态见当前状态文档。

本地Git管理源码、配置、测试和文档；运行环境、连接资料及实验原始数据在本机单独保存。
