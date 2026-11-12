# Visual Link

Windows与树莓派通过两条HDMI/USB视觉链路互传文件，支持中文本机操作页面、自动标定、分片确认、重传及完整文件交付确认。

**开始使用：[中文使用说明](使用说明.md)**

## 启动页面

在各自已登录的桌面上，从项目目录运行：

Windows（PowerShell）：

```powershell
.venv-win\Scripts\python.exe -m visual_link.web
```

树莓派（桌面终端）：

```bash
.venv-hardware/bin/python -m visual_link.web
```

两端分别打开 **http://127.0.0.1:8765**。页面中的文件路径和输出目录均为当前主机的本地路径。输入文件留空表示仅接收；两端仍通过视频链路交换连接信息和确认消息。

## 多分辨率交付

两端启动后自动交换设备能力，分别选择1920×1080或1920×1200，完成模式应用、实际采集验证和标定后传文件。页面显示启动协商、双向分辨率、实际采集模式及交付进度；推荐配置只保留设备选择和候选模式。

[四步完成状态](docs/交接/当前进度.md) · [步骤4交付与验收](docs/交接/04-交付.md) · [开发计划](docs/开发计划.md)

## 安装

需要Python 3.10或更新版本。当前设备已有运行环境，无需重复安装。

Windows：

```powershell
py -3 -m venv --copies .venv-win
.venv-win\Scripts\python.exe -m pip install -e .
```

树莓派：

```bash
python3 -m venv --copies .venv-hardware
.venv-hardware/bin/python -m pip install -e .
```

树莓派显示还需要系统GTK/PyGObject，首次配置见[硬件与桌面准备](docs/HARDWARE.md)。Python依赖版本在`pyproject.toml`中固定。

## 目录

- `src/visual_link/`：页面、通信协议、文件存储和平台设备代码。
- `configs/`：Windows及Pi推荐配置。
- `scripts/`：Windows桌面启动、部署及结果回收。
- `docs/`：硬件准备、部署、运行原理和协议说明。
- `runs/`：本地运行输出；保存分步实机验证及页面交付证据，入口见交接文档。
- `bear.jpg`：最近一次发送测试的原文件，保留在本地。

程序源码、推荐配置和使用文档纳入Git；运行环境、测试原文件和运行输出保留在本地。回归测试位于`tests/`；各步证据路径与验证范围见`docs/交接/`。

[运行原理](docs/运行原理.md) · [协议](docs/PROTOCOL.md) · [部署与回收](docs/DEPLOYMENT.md)
