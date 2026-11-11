# 单帧硬件接入与标定

2026-11-09最新实机状态：树莓派HDMI TX → Windows USB DirectShow RX标定及三组单帧文件传输通过，含1,000,103字节满容量文件；原始帧和源文件事后逐字节复核一致。详见[实机验收报告](../runs/hardware-live-20261109-01/REPORT.md)。Windows显示和反向单帧文件通信现也已实现并实测通过，详见[反向指南](REVERSE.md)；连续收发尚未验证。现阶段支持 Linux GTK / Windows Tkinter 单图保持、Linux V4L2/Windows DirectShow 单帧采集、标定分析及单帧文件恢复。

每个 `--output` 是一个全新的运行目录；失败也会留下记录，重试请更换目录。`tx/rx` 仍是离线图像入口；硬件使用 `display/capture/rx-one`。`fps` 配置当前不控制单图显示或设备采样率，不能据它报告硬件 FPS。

## 树莓派准备

在树莓派本地图形桌面终端运行，SSH 中不要自行猜测 DISPLAY/Wayland 会话。将 HDMI 输出设为 1920×1080、100% 缩放，关闭该次实验期间的屏幕保护和自动暗屏，移走其他窗口。当前显示程序不自动抑制系统空闲。

GTK 是系统依赖，不加入核心 pip 依赖。Debian/Ubuntu 可执行：

```bash
sudo apt-get install python3-gi gir1.2-gtk-3.0
python3 -m venv --copies .venv-hardware
.venv-hardware/bin/python -m pip install -e .
# 仅桥接系统PyGObject，避免导入整套系统包的依赖元数据；在树莓派原生目录执行。
visual_link_gtk_module=$(python3 -c 'import gi,pathlib; print(pathlib.Path(gi.__file__).parent.resolve())')
visual_link_gtk_site=$(.venv-hardware/bin/python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
ln -s "$visual_link_gtk_module" "$visual_link_gtk_site/gi"
.venv-hardware/bin/python -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk; print(Gtk.init_check())"
.venv-hardware/bin/python -m unittest discover -s tests -v
.venv-hardware/bin/python -m pip check
```

该环境仅链接系统 PyGObject（venv须由同一系统Python创建）。本机若用`--system-site-packages`会引入无关类型存根包并导致pip check失败，因此实测使用上述独立环境。已有环境不要重复覆盖gi链接；不要用 pip 安装另一个 OpenCV GUI 版本覆盖 headless 依赖。在 `configs/raspberry_pi.json` 中选择 `monitor`（从0开始）。显示会核对目标显示器和窗口均为1920×1080、scale=1及全屏状态，否则退出。Esc 提前退出返回失败；结束时销毁窗口，恢复桌面。全屏软件检查不证明 HDMI 输出或显示器颜色无变换。

## 先标定树莓派 TX → Windows RX

连接树莓派 HDMI 输出 → Windows 所接采集卡 HDMI 输入，采集卡 USB 接 Windows。先在 Windows 设备管理器确认设备，关闭占用摄像头的软件；`configs/windows.json` 的 `device` 是 OpenCV/DirectShow 从0开始的索引，不保证等于设备管理器排列，不自动扫描其他设备。

树莓派生成并显示标定图：

```bash
.venv-hardware/bin/python -m visual_link pattern --config configs/offline.json --seed 20261109 --output runs/cal-target-01
.venv-hardware/bin/python -m visual_link display --config configs/raspberry_pi.json --input runs/cal-target-01/pattern.png --seconds 120 --output runs/cal-display-01
```

在保持画面的120秒内，Windows 原生 PowerShell 运行：

```powershell
.venv-win\Scripts\python -m visual_link capture --config configs/windows.json --output runs/cal-capture-01
```

`capture.png` 保存 OpenCV 返回的完整 uint8 BGR 画面，尚未翻转、裁剪或展开色阶。`device.json` 记录请求/驱动报告格式、属性设置返回值、实际数组尺寸和 stride、预热帧数；`driver.log` 保留驱动输出。采集打开/读取/保存均在子进程内，总计受配置 `timeout` 限制；超时终止并回收子进程。正常或失败返回均释放设备。

Linux 请求 V4L2 `BGR3`；Windows 对应请求 DirectShow `MEDIASUBTYPE_RGB24`（GUID Data1 `0xe436eb7d`），不是把 Linux FOURCC 字符串直接用于 Windows。配置 `pixel_format=BGR3` 表示当前支持的三通道 BGR 像素路径。驱动报告的格式或尺寸不匹配则拒绝，没有 MJPG/YUYV 自动回退。Windows 格式属性未报告或未支持时会失败并留日志，此时必须继续诊断原生格式，不能将失败算作硬件适配成功。属性和数组检查仍不能证明设备上游无颜色变换。

标定图是公开测试目标，可通过共享目录/部署工具用于诊断。将同一份 `pattern.png` 放在分析端，运行：

```powershell
.venv-win\Scripts\python -m visual_link compare --config configs/windows.json --input runs/cal-capture-01/capture.png --reference runs/cal-target-01/pattern.png --output runs/cal-analysis-01
```

`result.json` 给出固定协议 ROI 的符号错误数、每级实际灰度范围，以及两种行序、±8像素平移的稀疏搜索最佳值。`dx=2,dy=0` 表示 `source[y,x] == oriented_capture[y,x-2]`，即历史基线；翻转仅在分析/接收时应用一次。搜索结果只是诊断，均匀图、图样不匹配或严重失真可能使估计无意义。检查最佳/次佳分数、固定 ROI 准确率和灰度范围后再判断；不会自动修改配置。

若仅方向不同，复制平台配置到新的文件并显式修改 `orientation` 为 `flip_vertical`；原始 capture.png 保留原行序。若最佳偏移不是历史(2,0)，当前冻结的协议 ROI 不适用，应保存证据并开发经过验证的新 profile，不能通过缩放图片“修复”。本轮未放宽固定协议参数。

## 再做单帧文件通信

标定确认后，在树莓派生成一个不超过1,000,103字节的文件帧并保持显示：

```bash
.venv-hardware/bin/python -m visual_link tx --config configs/offline.json --seed 20261109 --size 1000 --output runs/single-tx-01
.venv-hardware/bin/python -m visual_link display --config configs/raspberry_pi.json --input runs/single-tx-01/frame_00000.png --seconds 120 --output runs/single-display-01
```

Windows 在画面保持期间接收，不提供源文件、源图或种子：

```powershell
.venv-win\Scripts\python -m visual_link rx-one --config configs/windows.json --output runs/single-rx-01
```

成功须所有RS码块、帧SHA、总长度和文件SHA通过；结果在 `rx.json`，文件为 `recovered.bin`，原始帧仍在 `capture.png`。只接受能在这一张图中完整恢复的文件；多帧文件会报缺帧，不输出最终文件。无有效帧头时 expected/missing 是 null（未知）。当前只取预热后的单张图，不重试挑选成功帧，不宣称丢帧率、吞吐或单向延迟。

文件传输实验中，网络只用于部署、控制和回收结果；不要将业务源文件、发送图或种子交给 RX。事后由控制端比较 TX 输出 SHA 与 RX 输出 SHA。

## 初次连接限制与后续解除

开发阶段 Ubuntu 没有 `/dev/video*`；最初 SSH 访问 Windows/树莓派均在创建 socket 时返回 `Operation not permitted`，无法检查远端桌面、设备或部署新版本。日志在 `runs/hardware-backends-20261109-01/{pi-inspect,windows-inspect}.log`。这是当前执行环境限制，不能据此判定远端离线或认证失败。随后用户授权网络执行后，两端连接成功，已部署本版、完成真实标定和三组单帧文件验证。此旧错误已解除，不能再作为当前阻塞。最终证据在`runs/hardware-live-20261109-01/`。

API依据：[OpenCV 4.12 VideoCapture](https://docs.opencv.org/4.12.0/d8/dfe/classcv_1_1VideoCapture.html)、[DirectShow后端源码](https://github.com/opencv/opencv/blob/4.12.0/modules/videoio/src/cap_dshow.cpp)、[Microsoft RGB subtype标识](https://learn.microsoft.com/zh-cn/windows/win32/wmformat/media-type-identifiers)、[GTK全屏接口](https://docs.gtk.org/gtk3/method.Window.fullscreen_on_monitor.html)。

当前实测方向采用normal、dx=2、dy=0，未修改协议ROI。运行专用已校准配置保存在实机结果目录，适用范围限本次Pi TX → Windows RX链路；树莓派的第二块采集卡虽已取得BGR3画面，其原始行序有上下倒置现象，后续独立反向标定已确认flip_vertical、dx=2、dy=0，三组文件通过，详见REVERSE.md。
