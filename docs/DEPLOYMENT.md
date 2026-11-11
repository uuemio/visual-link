# 安装与部署

日常操作见[使用说明](../使用说明.md)，首次安装见[README](../README.md#安装)。

## 运行环境

- Windows：项目目录中的`.venv-win`，页面通过`scripts/windows_interactive.ps1`在已登录桌面启动传输。
- 树莓派：`~/visual-link`，使用`.venv-hardware`和真实桌面环境；GTK/PyGObject准备见[硬件说明](HARDWARE.md)。

两端分别使用`configs/windows-recommended.json`和`configs/pi-recommended.json`。

## 更新树莓派程序

在本项目目录执行，将用户名和地址替换为实际值：

```bash
bash scripts/deploy.sh 用户名@树莓派地址
```

首次使用SSH连接前，核对树莓派提供的主机指纹。部署脚本只复制源码、推荐配置、启动脚本、安装声明和使用文档，不复制运行环境或本地测试文件。解压更新不会删除远端已有文件。

Windows使用本机项目目录；使用共享文件夹时直接读取共享目录中的源码。更新后重启页面服务，再从本机浏览器打开 http://127.0.0.1:8765 。

## 回收运行结果

```bash
bash scripts/collect.sh 用户名@树莓派地址 runs/pi-collected-新编号
```

该命令将远端`runs/`复制到新的本地目录。管理网络仅用于部署和结果回收；文件传输及ACK/FINISH通过HDMI/USB链路完成。
