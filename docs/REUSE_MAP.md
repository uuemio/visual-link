# 复用映射
历史 rs_transport.py SHA256: dc76535f02ace5ff2f8b40bfc421eb0ef3227d601403d4dadfab14761c1c1523
历史 fps_codec.py SHA256: 063e6ccf71d83a421f43980267bca4f5ccbf667b3c16889f5f8402bca593d48c

rs_transport.symbols/wire_to_image/image_to_wire → core/mapping.py，固定TOP=3、16级，移除全局配置修改。
codec → core/rs.py；fps_codec.parity_table/parity → 同模块，保留批量GF路径。
encode_frame/decode_header/decode_wire/Decoded → core/protocol.py；编码使用批量parity，通用整文件头语义不变，严格decode拒绝任何失败块。
历史capture.py、GTK/fps_presenter不直接导入：有Linux专用依赖，留待阶段二适配。
证据：../usb-video-capture/HANDOFF.md、experiments/RS_RESULTS_20261105.md、experiments/FPS_RESULTS_20261105.md。

移植边界：未移植fps_codec.encode的独立帧文件语义；移植decode的批量合法码字检测及异常块纠错思路，保持通用file_sha/total。
历史默认TOP0/k223/sequential在新项目中固定为TOP3/k247/column；核心不再暴露可变全局配置。
验证：黄金向量seed=20261105,size=1000，填充seed=91；记录见runs/golden-sha256.txt，新旧wire及整图逐项一致。
RX逐块验证包括未使用信息区填充，较历史参考路径收紧；码块外尾部不承载数据。
源码哈希用于定位归档来源；历史程序仅供本机黄金交叉验证，独立部署测试不要求复制历史项目。

2026-11-09硬件接入：
- `capture.py` SHA256 `ea692cc70f818fa141b1b024bcf066f8ba385154ec1af30476bfd90d4e68bd2c`：借鉴capture_frame/run_worker的格式核验、预热、finally释放和进程总超时思路，新实现于backends/camera.py、worker.py及hardware.py；不导入历史Linux设备探测模块，不自动翻转。Windows单独使用DirectShow RGB24 subtype。
- `experiments/fps_presenter.py` SHA256 `d75bfd14a849621e839a01d3a561810871776f615fc8fb1ece222d9532b65afc`：复用GTK Pixbuf原尺寸呈现和窗口/scale检查思路，新实现backends/linux.py；增加显示器选择和全屏状态核验。当前仅单图保持，未移植连续时序、GNOME空闲抑制或after-paint统计。
- 新增calibration.py仅作像素诊断，不改变core协议及历史结果。

反向适配新增Windows Tkinter单图显示、交互任务、QueryDisplayConfig/SetDisplayConfig实际信号核验及恢复；依据微软官方接口，未移植或修改历史编解码。配置新增显式temporary_1080p布尔开关；测试覆盖桌面模式和活动信号在异常后的恢复。证据见runs/reverse-live-20261109-01/REPORT.md，灰度/ROI/RS协议黄金向量保持不变。

连续预编码适配：`backends/camera.py` 把已验证的设备协商/预热提取为持续生成器，`capture_one` 取首帧并关闭生成器；`streaming.py` 新建spawn采集/解码进程、有界队列、逐像素重复过滤及两阶段看守，继续使用原有decode/Reassembler。`presentation.py` 独立预取线程，GTK/Tk窗口内更新帧并记录本地软件时刻；未移植历史进程内ACK、after-paint统计或实时编码。线协议、ROI和RS参数未改，历史源码及结果未覆盖。
