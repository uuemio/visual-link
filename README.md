# Visual Link

通过双向 HDMI/USB 视频链路传输文件的软件，提供中文浏览器操作页面。两端可同时发送，程序自动协商分辨率、标定图像、确认分片并校验完整文件。

## 支持范围

- Windows：Python 3.10 及以上，使用 DirectShow 采集、Tkinter 显示。
- Linux：Python 3.10 及以上，使用 V4L2 采集、GTK 3 显示；桌面使用 X11，或支持 `wlr-randr` 的 Wayland 合成器。
- 视频模式：1920×1080、1920×1200；两个传输方向可使用不同分辨率。
- 每端具有 HDMI 输出及 USB 采集卡，显示输出与采集卡提供所用分辨率，采集支持 BGR3/RGB24 原生格式。

## 开始使用

1. 按[安装与部署](docs/DEPLOYMENT.md)安装两端程序。
2. 按[硬件准备](docs/HARDWARE.md)连接两条视频链路并选择设备。
3. 启动页面，打开 **http://127.0.0.1:8765**，按[使用说明](使用说明.md)传输文件。

Windows（PowerShell，在项目目录运行）：

```powershell
.venv-win\Scripts\python.exe -m visual_link.web
```

Linux（图形桌面终端，在项目目录运行）：

```bash
.venv/bin/python -m visual_link.web
```

## 项目内容

| 目录 | 内容 |
|---|---|
| `src/visual_link/` | 页面、传输协议和平台适配 |
| `configs/` | 设备配置示例 |
| `scripts/` | Windows 桌面启动和远程部署工具 |
| `docs/` | 安装、硬件和协议说明 |
| `tests/` | 开发维护用回归用例 |

会话文件在运行时生成于 `runs/`，与源码分开存放。

[运行原理](docs/运行原理.md) · [协议格式](docs/PROTOCOL.md)
