# 安装与部署

将完整项目复制或克隆到两台设备，各自在项目目录执行安装。虚拟环境在目标设备创建。

## Windows

安装 Python 3.10 及以上版本，包含 Tcl/Tk 组件。在 PowerShell 执行：

```powershell
py -3 -m venv .venv-win
.venv-win\Scripts\python.exe -m pip install -e .
.venv-win\Scripts\python.exe -m visual_link.web
```

## Linux

使用 X11 桌面，或支持 `wlr-randr` 输出管理协议的 Wayland 桌面。Ubuntu/Debian 的 X11 环境安装命令：

```bash
sudo apt-get install python3-venv python3-gi gir1.2-gtk-3.0 v4l-utils x11-xserver-utils
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m visual_link.web
```

Wayland 桌面另安装 `wlr-randr`。其他 Linux 发行版安装对应系统包。运行用户需具备 `/dev/video*` 采集设备访问权限。

## 配置与启动

Windows 使用 `configs/windows-recommended.json`；Linux 使用 `configs/pi-recommended.json`，其中 `pi` 是配置文件名，后端为通用 Linux。

按[硬件准备](HARDWARE.md)设置设备和显示器编号。在已登录的图形桌面启动，两端分别打开 http://127.0.0.1:8765 。更换端口使用 `--port 8766`。

## 更新

两端更新到相同版本，重新执行各自的 `python -m pip install -e .`，然后重启页面服务。

也可从 Bash 使用 SSH 部署到 Linux 用户主目录下的 `visual-link/`：

```bash
bash scripts/deploy.sh 用户名@主机地址
```

部署后在目标设备执行安装命令。会话输出使用本地支持硬链接的文件系统，例如 NTFS 或 ext4。源码更新与接收文件分别管理。
