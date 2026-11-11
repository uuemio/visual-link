# 连续通信：预编码链路

`display-stream` 在同一窗口循环播放目录内按名称排序的 PNG，`config.fps` 控制最低保持间隔，遇到延迟不跳过帧。独立线程预取，默认队列2张；GTK/Tk仍在主线程更新。软件换图请求时间记录在 `presentations.jsonl`，不是 HDMI 扫描输出时间；实际FPS须根据本轮日志和采集另行计算。模式与几何检查沿用单帧后端；当前 Windows 使用 configs/windows-current.json，保持用户设置的 1920×1080，禁止临时切换和恢复 1200p。窗口就绪写 `ready.json`，模式切换后仍需至少3秒锁定等待。

`rx-stream` 在一次设备打开/协商和5帧预热后持续采集，独立解码进程通过有界队列接收图像，默认2张，最多16张。采集端先逐像素比较上一张已入队图像，仅完全相同的画面计入 `identical_pixels_filtered`，不再重复解码；任何一个像素变化均保留。比较对象仅在成功入队后更新，队列拥堵不会把尚未送达的变化帧误作已处理。队列满时丢弃新到图像并增加 `queue_full_drops`；驱动内部的丢帧无法由此计数。两个子进程各有阻塞看守，结束后有界等待排空，超时终止并回收。`config.timeout` 用作启动/停滞/排空超时，`--seconds` 是从启动开始的总采集窗口，含启动和预热时间。

RX只接收自身配置和USB像素，无业务源文件、发送图、种子或网络分片输入。每张入队图像都做完整RS与帧SHA检查；过滤的相同像素不冒充额外的完整校验帧。一致重复只计数，文件元数据不一致或坏帧被记录拒绝。收到全部帧后核对整文件长度/SHA，原子发布 `recovered.bin`，继续观察至窗口结束。无完整文件则非零退出；没有有效头时 `expected/missing=null`。运行目录必须新建，默认拒绝覆盖。

文件完整恢复的 `status=complete` 与稳定性分开：`stability_pass` 要求文件完整、无坏帧、无队列丢弃且所有入队图像处理完。成功后如采集/解码超时，最终状态仍失败，已校验的文件可保留供诊断。`frames.jsonl` 记录每张被解码图像的本地采集时间、队列等待、解码耗时、帧号/纠错或拒绝原因，保留第一张拒绝原图；不保存每张重复原图，避免无限磁盘增长。载荷重组仍随文件大小占内存，日志随运行时长增长。

## 正向低速试跑

先在树莓派离线编码源文件（超过1,000,103字节可产生多帧），另存配置并显式把 `fps` 改为1。不要直接使用模板的历史10FPS作为新设备稳定档位。

```bash
.venv-hardware/bin/python -m visual_link tx --config configs/offline.json --input source.bin --output runs/new-frames
.venv-hardware/bin/python -m visual_link display-stream --config runs/pi-tx-1fps.json --input runs/new-frames --seconds 120 --queue-size 2 --output runs/new-display
```

在真实Pi桌面启动，SSH所需Wayland环境见已有实机控制脚本。确认ready并等待信号稳定后，在Windows执行；发送保持时间须覆盖控制调度、锁定和整个RX窗口。`ready.json` 可能已存在一段时间，不能据其存在假定剩余发送时间足够。须在发送到期前完成RX；不要把发送退出后的桌面坏帧计为稳定通过：

```powershell
.venv-win\Scripts\python -m visual_link rx-stream --config configs/windows.json --seconds 60 --queue-size 2 --output runs/new-rx
```

RX结束后才能回收源文件，与恢复结果逐字节比较。网络仅部署、控制和事后核对。反向RX使用已标定 `flip_vertical` 配置；Windows本地可直接 `display-stream`，SSH使用 `scripts/windows_interactive.ps1 -Mode display-stream`，此时 `-InputImage` 指向帧目录，保留外层模式恢复看守。

## 验收边界

当前实现循环同一个预编码文件，无ACK或按接收状态重传。后续轮次可补齐早期丢失帧，所以文件成功不能解释为首轮无丢帧。协议v1无周期/会话编号，不能统计光学链路每轮呈现的精确丢帧率。`duplicates` 是完整校验后重复编号计数，`queue_full_drops` 是本机软件丢弃，`missing` 是文件最终缺帧，三者含义不同。

`unique_file_bytes_per_window_second` 仅为本次完整文件字节数/采集窗口秒数，循环同文件不重复增加净字节数，不能据此声称饱和链路吞吐。两端时钟未同步，不计算跨主机单向延迟。实时编码与预编码性能必须分开验证，当前接口只覆盖预编码。

按1→2→5等低速逐档，每个候选档位至少三轮60秒，再做30分钟；每轮新文件/新目录，记录环境、源哈希、显示/接收统计、阶段耗时与事后逐字节核对。只有实际完成的轮次写入验收结论。短时试跑、合成测试、历史10FPS均不能替代上述稳定性测试。
