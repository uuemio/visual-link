# Windows → 树莓派单帧通信

## 当前运行约定（2026-11-09 用户更新）

Windows 通信输出已由用户设为 1920×1080；后续保持该设置。当前入口使用 `configs/windows-current.json`（monitor=1/device0，沿用既有布线，temporary_1080p=false）。启动/运行/退出不主动修改分辨率，不执行旧的 1200p 恢复脚本；后端仍检查实际 HDMI 活动信号和 identity 缩放。检查不符合要求时记录并退出，不自动改模式。本次未操作硬件，新配置尚未实机重验。

以下临时切换及 1200p 恢复描述是原实验流程；当前操作遵循本节。历史配置与证据保持原样。


2026-11-09已实测通过，完整证据见[验收报告](../runs/reverse-live-20261109-01/REPORT.md)。Windows显示器索引1（DISPLAY2）→树莓派采集卡/dev/video0；三组1,000 / 1,000,103 / 65,537字节文件独立恢复，接收后由控制端逐字节比较一致。

## 反向链路的独立配置

反向RX必须先垂直翻转，再取固定接收ROI。实测 `flip_vertical, dx=2, dy=0`，有效区2,065,686像素符号错误0；不能沿用正向normal方向。当前设备的冻结配置在树莓派 `runs/reverse-live-20261109-01/pi-rx-calibrated.json`，控制端结果目录也保存了副本。更换设备、输出口或显示模式后重新标定。

Windows原有两个显示器均为1920×1200。只修改桌面分辨率到1080p仍会得到1200p HDMI活动信号和GPU等比缩放，实际采集出现黑边和重采样。新后端同时检查源桌面和活动信号：需要1920×1080、identity缩放；显式设置 `temporary_1080p: true` 才允许临时改变桌面和HDMI时序。未开启该选项时，尺寸或信号不满足条件就拒绝启动。模式切换不写注册表或显示配置数据库，退出时恢复。

Windows TX本次配置为 `runs/reverse-live-20261109-01/windows-tx-monitor1.json`，monitor=1、temporary_1080p=true。后端使用标准库Tkinter PNG呈现，无额外GUI pip依赖；使用每显示器DPI感知和物理窗口坐标，拒绝窗口尺寸不符。API依据：[微软SetDisplayConfig](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setdisplayconfig)、[活动视频信号结构](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/ns-wingdi-displayconfig_video_signal_info)。

## 启动与采集

Windows本地已登录桌面终端，可直接执行：

```powershell
.venv-win\Scripts\python -m visual_link tx --config configs/offline.json --seed 2026110902 --size 1000 --output runs/reverse-next-tx
.venv-win\Scripts\python -m visual_link display --config runs/reverse-live-20261109-01/windows-tx-monitor1.json --input runs/reverse-next-tx/frame_00000.png --seconds 60 --output runs/reverse-next-display
```

通过SSH不能直接在物理桌面显示：本机SSH会话只看到WinDisc 1024×768虚拟屏幕。可用已实现的交互任务启动器，在同一已登录用户桌面执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/windows_interactive.ps1 -Config configs/windows-current.json -InputImage runs/reverse-next-tx/frame_00000.png -OutputDirectory runs/reverse-next-display -Seconds 60
```

ExecutionPolicy仅用于该PowerShell进程，不更改用户或系统永久策略。启动器不接收或保存密码；用户须已登录，后台服务/SSH隔离会话不能替代物理桌面。它生成唯一临时计划任务，正常完成后删除；外层Python进程负责超时终止显示子进程并恢复桌面与完整HDMI配置。若执行环境强制杀死整个任务或主机断电，需根据保存的初始显示记录人工核对模式，不能把异常终止算作恢复成功。

先等输出目录出现 `ready.json`，再为本次链路等待至少3秒信号稳定，随后在树莓派运行：

```bash
.venv-hardware/bin/python -m visual_link rx-one --config runs/reverse-live-20261109-01/pi-rx-calibrated.json --output runs/reverse-next-rx
```

`ready.json`只表示软件窗口原尺寸就绪，不证明HDMI接收已锁定。实际模式切换的首次过早采集曾取得全黑帧，被无效帧头检查拒绝，未输出恢复文件；加入上述稳定等待后，三组正式文件均通过。固定5帧采集预热也不能替代模式切换后的锁定等待。每次使用新的输出目录，等待当前显示任务结束、原模式恢复，再开始下一次显示。

标定可以使用公开的pattern/compare工具；文件RX仅提供自身配置与输出目录，不读取源图、源文件或种子。业务文件仅走HDMI/USB；完成后才回收数据作控制端比较。需要整文件不超过1,000,103字节，超过单帧容量的文件仍不能用rx-one完成。

## 结果与范围

本轮Ubuntu、Windows、树莓派各23项测试通过，覆盖桌面模式/活动信号异常恢复，Windows和树莓派pip check通过。三组成功帧均为4049个数据RS块通过、帧SHA/文件SHA通过、完整wire字节错误0、数据纠正符号0。原有两个1920×1200显示器的几何、活动信号和identity缩放均已恢复，临时显示任务剩余0。

目前正反两个方向的单帧通信均有实机证据；连续吞吐、长期稳定性、ACK/重传尚未实现或验证。三组成功文件不能掩盖先前的黑帧拒绝，也不能据此宣称所有尝试均成功或链路长期零丢帧。
