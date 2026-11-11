# 安装与部署

日常操作见[使用说明](../使用说明.md)。当前Windows/Pi已经完成安装和双向硬件传输。

## 当前环境

- Windows：项目目录中的`.venv-win`，通过`scripts/windows_interactive.ps1`在已登录桌面运行。
- 树莓派：`~/visual-link`，使用`.venv-hardware`和真实Wayland桌面。
- Ubuntu：开发环境`/tmp/visual-link-venv`。

安装Python依赖的命令见[README](../README.md#安装)。Linux显示需要系统GTK 3/PyGObject；树莓派硬件环境使用系统gi。

## 更新树莓派程序

在Ubuntu的visual-link目录执行，将用户名和地址替换为实际值：

```bash
bash scripts/deploy.sh 用户名@树莓派地址
```

脚本将源码、配置、测试和文档复制到远端`~/visual-link`。SSH首次连接时核对树莓派提供的主机指纹。当前本机连接资料位于工作区`.local/connections/`。

Windows使用VMware共享文件夹中的项目文件。两端分别选择`windows-recommended.json`和`pi-recommended.json`。

## 回收运行结果

```bash
bash scripts/collect.sh 用户名@树莓派地址 runs/pi-collected-新编号
```

历史实验与最新部署版本见[实施状态](../IMPLEMENTATION_STATUS.md)。
