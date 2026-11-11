# 硬件与桌面准备

## 接线与显示

- Windows HDMI输出 → 采集卡A HDMI输入 → 采集卡A USB接树莓派。
- 树莓派HDMI输出 → 采集卡B HDMI输入 → 采集卡B USB接Windows。

Windows传输显示器应为1920×1080、100%缩放，物理输出也应为1080p identity。推荐配置使用第二个屏幕，`monitor=1`，`temporary_1080p=false`。

树莓派使用1920×1080、scale=1的真实桌面。推荐配置使用`monitor=0`。在本地图形桌面终端启动程序，保留桌面会话；传输期间关闭自动暗屏和屏幕保护，避免其他窗口遮挡通信图像。

采集设备索引在各端推荐配置的`device`中指定，从0开始。关闭占用采集卡的其他软件。程序在连接时自动标定方向、位置和灰度阈值。

## 树莓派GTK环境

GTK/PyGObject是系统显示依赖，不通过pip安装。首次安装时，在树莓派原生文件系统的项目目录执行：

```bash
sudo apt-get install python3-gi gir1.2-gtk-3.0
python3 -m venv --copies .venv-hardware
.venv-hardware/bin/python -m pip install -e .
visual_link_gtk_module=$(python3 -c 'import gi,pathlib; print(pathlib.Path(gi.__file__).parent.resolve())')
visual_link_gtk_site=$(.venv-hardware/bin/python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
ln -s "$visual_link_gtk_module" "$visual_link_gtk_site/gi"
.venv-hardware/bin/python -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk; print(Gtk.init_check())"
```

虚拟环境须由同一个系统Python创建。当前已安装的环境不要重复创建或覆盖`gi`链接。Python包使用headless OpenCV，显示由GTK或Windows Tkinter负责。

## 接收文件与完成条件

输出目录所在文件系统须支持硬链接，才能将校验后的文件原子发布为`received.bin`。每次传输使用新的输出目录。

页面显示成功需要传输进程正常退出、`result.json`为`complete`，以及本次各方向交付确认完成。仅接收的一端也需要通过显示控制图像向发送端确认分片和文件交付。

操作步骤见[使用说明](../使用说明.md)，字段与交付确认协议见[协议](PROTOCOL.md)。
