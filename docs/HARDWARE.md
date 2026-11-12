# 硬件与桌面准备

## 接线与显示

- Windows HDMI输出 → 采集卡A HDMI输入 → 采集卡A USB接树莓派。
- 树莓派HDMI输出 → 采集卡B HDMI输入 → 采集卡B USB接Windows。

Windows推荐配置默认1920×1080、100%缩放，第二个屏幕`monitor=1`，`temporary_1080p=false`。模式应用接口可临时切换1920×1080或1920×1200，并检查桌面像素与物理HDMI active尺寸一致、identity scaling。

树莓派推荐配置默认1920×1080，使用scale=1、normal transform的真实桌面。推荐配置使用`monitor=0`。在本地图形桌面终端启动程序，保留桌面会话；传输期间关闭自动暗屏和屏幕保护，避免其他窗口遮挡通信图像。

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


## 能力查询与手动模式应用

能力读取不要求当前输出等于配置尺寸。Windows通过`EnumDisplaySettingsW`枚举所选显示器，通过`QueryDisplayConfig`读取实际源/目标时序；采集通过所选DirectShow设备的`IAMStreamConfig`枚举原生输出格式及帧率范围，无新增Python依赖。显示相关命令须在已登录桌面执行，SSH隔离桌面不是通信桌面。

Linux使用真实桌面的`wlr-randr --json`（Wayland）或`xrandr`（X11），以GTK所选监视器的位置匹配输出连接器，采集通过`v4l2-ctl`读取。所需命令不存在时安装对应系统包；不向`/boot`写入自定义时序。远程启动必须传入真实会话的`XDG_RUNTIME_DIR`、`WAYLAND_DISPLAY`或`DISPLAY`，不能另建虚拟桌面。

在Pi真实桌面执行（每次使用新的结果目录）：

```bash
.venv-hardware/bin/python -m visual_link capabilities --config configs/pi-recommended.json --output runs/pi-capabilities-new
.venv-hardware/bin/python -m visual_link apply-mode --config runs/pi-1200.json --output runs/pi-apply-new --seconds 30
```

`runs/pi-1200.json`由推荐配置复制，仅增加`"tx_mode":{"width":1920,"height":1200}`；接收1200p时独立设置`rx_mode`。`apply-mode`默认显示标定图，也可用`--input image.png`显示本方向原生尺寸图像。可选`--refresh-hz`单位Hz；省略时Windows优先接近60Hz的声明模式，Linux使用该尺寸的最高声明刷新率。上下文退出（含异常）恢复原模式，保留`mode.json`、`ready.json`和`result.json`。Windows还验证物理输出，无法取得identity时拒绝候选。

Windows使用已有交互桌面脚本：

```powershell
.\scripts\windows_interactive.ps1 -Config configs\windows-recommended.json -OutputDirectory runs\windows-capabilities-new -Mode capabilities -Seconds 2
.\scripts\windows_interactive.ps1 -Config runs\windows-1200.json -OutputDirectory runs\windows-apply-new -Mode apply-mode -Seconds 30
```

`windows-1200.json`同样只在推荐配置上设置tx方向参数；页面尚不调用模式应用接口，自动协商由步骤3接入。

## 信号变化后的采集更新

对向切换且显示就绪后，关闭旧采集generator/worker，再运行：

```bash
.venv-hardware/bin/python -m visual_link capture-mode --config runs/pi-1200-rx.json --output runs/pi-capture-new
```

该命令重新打开设备，请求rx尺寸与原生BGR3（Windows为RGB24子类型），丢弃5帧预热后读取实际帧。严格格式规则沿用现有流程，不自动回退压缩或YUV。`probe.json`保存请求尺寸、驱动reported、实际shape/strides、采样读数以及候选是否匹配；采集失败时看`device.json`与`driver.log`。驱动允许设置不代表实际支持：尺寸不匹配会保存证据并拒绝该候选。支持的实际尺寸可通过`refresh_capture(...,require_match=False)`用于诊断，不应据此提交失败候选。

`capabilities`的`display.declared`与`capture.declared`是驱动声明，`current_output`是当时桌面或设备输出；DirectShow的current_output仅表示未连接输出pin默认媒体类型。V4L2设备若不支持HDMI输入时序查询会保留失败返回码，不能以USB输出格式推断输入时序。真实像素以采集`actual/shape`和标定、解码为准。`sampling.frames_read/elapsed_seconds`是软件读取（含预热）情况，不是HDMI帧时间戳或链路吞吐。

本轮Windows/Pi实测与统一接口见[步骤2交接](交接/02-设备能力.md)。
