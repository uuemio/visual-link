# 线格式 v1
画布1920×1080 uint8。TX [3:,2:]；RX [3:,:1918]，行序修正在裁剪前。
码本0,17,...255；每字节高/低半字节映射两个像素；最近邻判决。
1,032,843逻辑字节reshape(959,1077)转置后发送；接收逆变换。
头部struct `>8sBBBIIIQ32s32s`：magic GRAYRS01、版本1、n、k、index、count、length、total、帧SHA256、文件SHA256。
头部95字节补零到127，RS(255,127)。随后4049个RS(255,247)码字；净容量1,000,103字节，尾部93字节。
GF(256), prim=0x11d, generator=2, fcr=0, c_exp=8。数据随机填充使用NumPy default_rng(seed).bytes；依赖锁定2.2.6。
业务随机源独立使用SHA256(ASCII十进制非负seed + ':' + uint64大端counter)，counter从0开始，拼接并截到size。
RX不需要源数据或seed。每块通过RS，帧SHA和最终文件SHA/总长度通过才交付。
相同文件SHA/长度/帧数/n/k为单次接收身份；一致重复计数，冲突/混合/缺帧拒绝。
无帧时预期数量及丢失数未知。无ACK/重传，整帧丢失不能由RS恢复。暂不支持跨会话ID。
不纠正码块之后的93字节：它不属于业务或受保护填充；码块内填充同样必须通过RS。

# 引导控制 v1（1080p双向实机验收通过）

引导格式不使用数据帧ROI、灰度档位或行序配置。白色画布中居中放置48行×92列单元矩形，外圈全黑，四周至少2单元白色静区。单元边长取`min(width//96,height//52)`，至少4像素。内部46×90单元按行填入黑0/白1位，最高位先发；前4080位是两个RS(255,223)码字，其余60位为黑色。接收端用Otsu阈值寻找黑色外轮廓，按矩形定位并尝试四种旋转及镜像。当前仅支持轴对齐、完整外框，未实现透视/任意角度或外框裁切恢复。

446字节保护内容：`!4sBH`头（magic=`VLBC`、引导版本1、JSON字节长度），紧随UTF-8 JSON、4字节大端CRC32（覆盖头和JSON），剩余补零。长度上限435字节。前后各223字节分别RS编码；GF参数沿用reedsolo默认值。CRC、magic、版本、长度和零填充均验证后才交付消息。

JSON为六元素数组，字段按顺序如下：

| 字段 | 定义 |
|---|---|
| sender | 本机启动随机UUID的32位小写hex |
| peer | 接收方UUID，HELLO为null |
| sequence | 1到2³²−1；HELLO=1、CAPS=2、REPORT=3、配置消息=4..7 |
| ack | 关联阶段序号；HELLO=0、CAPS=0/2、REPORT=0/3，配置消息见后文 |
| kind | 当前处理HELLO、CAPS、REPORT、PROPOSE、ACCEPT、COMMIT、READY；ACK/FINISH见可靠传输小节 |
| body | HELLO为空对象；CAPS为以下能力对象 |

能力对象字段：versions（实现的协议版本），profiles（实现的数据档位），modes（软件支持的宽高对），receive_window（接收窗口上限）。当前默认值为`[1]`、`["v1-gray16"]`、`[[1920,1080]]`、1。这些字段描述软件支持范围；connect另提供所选设备观测、measured_ms测量和自动标定，见下文。connect的versions为[1,2]。

会话ID为双方UUID按字典序排序并以冒号连接；较小UUID是后续配置协商的协调端。

| 当前状态 | 输入/条件 | 动作与下一状态 |
|---|---|---|
| discovering | 合法HELLO | 保存对端，进入exchanging |
| exchanging | 本端定时发送 | 交替重复HELLO/CAPS；已收CAPS后ack=2 |
| exchanging | 合法CAPS，双方能力有交集且ack=2 | 保存对端能力，进入capabilities |
| exchanging/capabilities | 能力无交集 | incompatible，仍重复控制图 |
| 任意 | 当前对端重复消息 | 幂等处理；同会话能力改变则拒绝 |
| 任意 | 新UUID的HELLO | 清空对端确认及能力，旧UUID退役，重新exchanging |
| 任意 | 发给其他对端、自己发送或退役UUID | 忽略 |

丢失示例：A/B交换HELLO→A的CAPS丢失→B重复CAPS(ack=0)→A重复CAPS(ack=2)→B重复CAPS(ack=2)→双方capabilities。即使一方先完成，仍持续重复控制图，另一方可继续收敛。该阶段仅面向直连两个端点，进程运行期间保留退役UUID。

`bootstrap`每秒换一次控制图，在指定时间内持续应答，以最终状态判断本端完成；不会提前退出让对端失去确认。记录control.jsonl、device.json、ready.json和result.json。采集/引导解码在spawn进程中，消息队列上限8，满队列丢弃由控制重复补偿；主GUI检查采集心跳并在超时/退出时回收子进程。result中的complete仅表示能力交换，不表示标定、配置提交或业务传输完成。

## 自动探测与双向标定（connect）

`connect`在引导交换前查询所选显示输出，使用同一采集后端确认所选设备实际协商的1080p/BGR格式；测量4张预热后采集帧之间的3个间隔中值，并做一次v1编码及本地BGR完整接收路径校验（控制搜索、阈值查表、RS及哈希）。CAPS可选字段`measured_ms`依次为完整帧编码、完整接收处理（含本地BGR展开、控制搜索、像素转wire及RS/哈希）和采集间隔毫秒值，必须为三个有限正数；缺省为空，兼容旧CAPS。设备和显示详细观测保存在probe.json。接收处理用本地编码图模拟BGR输入和已知发送ROI，不计磁盘写图、GUI及完整文件落盘，也不替代实际标定。此测量仅对应所选设备、当前已实现的1080p档位，不代表枚举所有硬件模式或持续吞吐上限；窗口默认1，connect可通过receive_window配置1..64；此处只确定初始上限；connect运行时的发送限制见后文。

能力交换完成后，每4秒预留1秒显示本机标定图，其余时隙继续轮询HELLO/CAPS/REPORT。标定图使用发送方32位hex会话UUID转成整数作为NumPy default_rng种子，沿用16级空间随机图生成器。接收器只根据视频HELLO生成预期图，不读取对端文件或通过网络获取图像。会话UUID变化时重新生成目标并清空标定状态。

接收器在两个行序、±8整数像素偏移范围内按稀疏样本相关系数搜索。最佳系数至少0.995，与次佳相差至少0.05；仅接受完整保留v1源ROI的映射。学习16级灰度实际最小/最大/均值，要求相邻级不重叠，以相邻最大/最小中点确定15个判决阈值，再校验完整2,065,686像素，符号错误必须0。阈值及接收ROI只保存在接收端会话结果；`calibration.receive_wire`应用这些参数恢复相同物理映射的v1/v2 wire；connect业务接收使用此接口。

REPORT的sequence固定3；body为`profile,orientation,dx,dy,pixels,symbol_errors`，profile=`v1-gray16`、orientation为normal/flip_vertical、dx范围0..2、dy范围0..3（确保完整源ROI不裁切）、pixels=2065686、symbol_errors=0。ack初始0，收到对端REPORT后置3；重复REPORT必须内容相同。接收方本地标定成功、收到对端成功REPORT且收到ack=3后，`calibrated=true`。底层发现状态仍为capabilities，标定进度通过这些显式字段表示。REPORT在全部阶段持续重复，以恢复丢失；对端启动UUID变化后，报告和确认状态全部清空。

connect在双向标定完成后继续配置提交；完成状态要求配置ready、本端已开始接收的文件完整且FINISH已被确认，以及可选发送文件收到对端FINISH。就绪前继续发送标定和控制图，就绪后保持控制图与可选业务图的调度。

## 配置提交与会话数据 v2（connect）

`connect`使用下列流程。`bootstrap`仍只交换HELLO/CAPS。标定REPORT的profile仍为`v1-gray16`，表示同一物理像素档位；CAPS versions包含2才允许协商带身份的数据头。

两个端点按UUID排序。配置1用于较小UUID→较大UUID，配置2用于相反方向；配置ID仅在本次会话有效。提议body固定为`{"directions":[[1,"v1-gray16",fps,window],[2,"v1-gray16",fps,window]]}`。每行分别表示配置ID、物理档位、最大发送帧率、接收窗口上限。两个方向独立计算：fps不超过2，亦不超过发端编码耗时、收端解码耗时和采集间隔各乘2所得周期的倒数；窗口取该方向接收端CAPS上限。短时测量只用于保守初始上限，不是持续性能或动态调节结论。当前发送间隔不短于1/fps；默认业务图保持0.75/fps秒，控制图保持0.25秒。控制机会优先轮询ACK/FINISH，每4秒保留HELLO用于重启发现。窗口作为首次发送额度，后续由ACK更新，实际执行见可靠传输小节。

所有配置消息携带相同的完整body；序号/ack固定如下，同一会话不允许更改配置：

| 发送方 | 消息 | sequence | ack | 收端条件和动作 |
|---|---|---|---|---|
| 协调端 | PROPOSE | 4 | 3 | 双向标定已确认，校验能力和两个方向限额，保存提议，accepted |
| 对端 | ACCEPT | 5 | 4 | proposing且配置完全一致，committing |
| 协调端 | COMMIT | 6 | 5 | accepted且配置一致，committed；尚不发送业务 |
| 对端 | READY | 7 | 6 | committing且配置一致，协调端接收ready，等待最终READY回执后发送 |
| 协调端 | READY | 7 | 7 | committed且配置一致，对端ready且can_send，重复回送READY(7,7) |
| 对端 | READY | 7 | 7 | ready且配置一致，协调端can_send，可以发送该方向数据 |

每端反复发送HELLO/CAPS/REPORT及当前配置阶段消息。重复或迟到的旧阶段消息不回退状态；先于前置阶段到达的消息不推进状态，靠重复恢复。例：PROPOSE丢失→协调端重复PROPOSE→ACCEPT丢失→对端重复ACCEPT→COMMIT丢失→协调端重复COMMIT→READY丢失→对端重复READY→协调端重复最终READY。协调端在can_send前优先重复最终READY；对端就绪后的READY使用ack=7作为收到最终READY的回执。各端首次业务前先显示一次本端READY。ready表示本端可以接收，can_send还要求视频确认对端已可接收；新UUID同时清空这两项。此READY回执扩展要求两端同步升级，旧端不会回送新确认。校验序号、ack、角色、目标和配置边界；新UUID的HELLO清空标定、配置、接收重组与发送缓存，退役UUID不再生效。已完成文件以旧peer UUID归档保留。

v2保留v1像素、RS、容量和前95字节字段布局，magic改为`GRAYRS02`，version=2。原保护头的32字节补零区改为`>16sI12s`：

| 字段 | 定义 |
|---|---|
| session | 双方排序UUID以冒号连接后的ASCII SHA256前16字节 |
| config_id | 该方向配置ID，uint32大端且非零 |
| file_id | 文件SHA256前12字节；完整SHA256仍在原文件头中 |

三个字段均受现有RS(255,127)保护。接收端先用自己自动测得的orientation/roi/thresholds恢复wire，随后校验RS、帧SHA、ready状态、会话、接收方向配置ID及file_id；只把通过的分片交给重组器，完整长度和文件SHA通过后写出`received.bin`。重组器禁止混合会话和文件ID，v1黄金向量保持不变，v1帧不能进入协商后的业务路径。

`connect --input 文件`可同时发送一个文件；省略input则仅协商和接收。connect源文件以固定块计算完整SHA，保留只读文件描述符并在后台逐片读取；逐片读取前后检测源文件大小/修改时间，变化即失败。接收端按分片偏移写入独占创建的received-<peer>.part，只在内存保留分片元数据。普通离线Reassembler接口仍保留。单线程后台只预编码下一张图，GUI不等待整文件编码；已确认分片的编码缓存及时释放。尚未完成编码时继续显示控制，不消耗窗口或启动该分片的重传计时。每会话只提交一对配置；运行中更换配置、保留旧配置在途分片待后续实现；发送侧在协商限额内的动态控制见后文。

## 可靠传输（connect，v2数据头不变）

`transport.py`持有每方向一个文件的选择性重传状态，复用引导控制封装。消息的sender/peer必须匹配当前ready会话，旧UUID或发给其他端点的反馈忽略。业务间隔遵守协商FPS，默认保持0.75/fps秒，其间使用0.25秒控制图轮询反馈；两项可由本机data_hold_seconds/control_hold_seconds设置为有限正数。data_hold_seconds是1 FPS基准保持秒数，随协商周期放大。READY确认优先，每4秒保留HELLO，其他控制机会轮询ACK/FINISH；完成后继续反馈直到运行时间结束。SSH不参与任何传输状态。

ACK固定sequence=8、ack=0，body字段如下：

| 字段 | 定义 |
|---|---|
| s/c/f | 数据头session、方向config_id、file_id，与被确认文件一致 |
| b | 连续收到的分片数；所有index < b均已通过RS与帧SHA |
| m | 16位小写hex，64位选择性位图；bit i确认分片b+i |
| w | 从b开始可接收的窗口跨度，0..协商窗口（首版最多64） |
| r | 当前文件反馈修订号；仅当位图、连续位置或额度变化时递增 |

首次额度来自已提交配置。未确认在途数不得超过窗口，新分片index必须小于b+w；接收端也拒绝超出跨度的分片。额度是绝对窗口，重复ACK不会累加额度。较旧/重复ACK可补确认，但不能回退连续位置或重新放大额度；确认未发送分片、非法位图或越界额度拒绝。配置可选`receive_window`取1..64，默认1。已授权的在途分片在额度为0时仍可重传，新分片等待新修订的额度。

每次业务机会优先选择超过当前RTO且未确认的分片，否则发送窗口内的新分片；已确认分片不再发送。ACK丢失通过重复反馈恢复，整帧丢失或坏帧通过超时重传恢复，选择性位图使乱序到达的分片无需再发。

connect启用发送侧自适应控制，不修改已提交配置、数据头或ACK字段。速率起始为该方向协商上限，范围为上限的1/4到上限；在途数量限制起始min(2,协商窗口)，范围1到协商窗口。该限制额外约束新分片，不缩小接收端的已授权绝对跨度；已授权未确认分片仍可重传，w=0禁止新分片。

RTO起始12秒。收到新增确认时，只对从未重传的分片取本机发送至处理ACK的时延（Karn规则），一次累计确认取其中最大值。首样本初始化SRTT=样本、RTTVAR=样本/2；以后先更新RTTVAR=0.75×旧值+0.25×|旧SRTT−样本|，再更新SRTT=0.875×旧值+0.125×样本。RTO取min(30秒,max(3秒,4/协商FPS,SRTT+4×RTTVAR))；覆盖本机处理和视频控制调度延迟，不解释为物理线缆RTT。

实际选择超时重传时，每个超时退避周期最多减速一次：速率×0.8，在途限制减半（取整，下限1），RTO加倍（上限30秒），清空恢复计数。准备编码、查看候选帧不触发退避。每新增确认达到当前在途限制后，限制加1、速率增加协商上限的5%，均不越过协商上限。重复/迟到ACK只能补充未确认分片，不重复产生样本或恢复额度。新UUID重建整个控制状态。Transport统计和data.jsonl的pacing/tx条目记录fps、flight_limit、rto、srtt、rttvar和样本数。独立Sender默认固定策略以保留离线历史回放；connect显式启用adaptive=True。

这只实现协商限额内的发送调节；接收方主动改变w、重新提交配置和旧配置在途保留尚未实现。

接收端每片写入暂存文件后即可更新ACK（仅分片接收确认，不表示持久交付）。全片到齐后，独立后台任务分块重读校验长度/完整SHA、fsync数据并用不覆盖目标的原子硬链接发布received.bin；主显示线程继续ACK/控制调度。发布失败则无FINISH，保留暂存证据。主线程观察发布完成、保存received.json后，重复发送FINISH(sequence=9,ack=0)。body为`s,c,f,sha,total,count`，其中sha是完整64位hex文件SHA256，total为文件字节数，count为分片数。发送端只接受与本端源文件完整身份一致的FINISH，且必须已经发送全部分片；随后停止业务发送并持续回送完全相同body的FINISH(sequence=9,ack=9)。接收端收到此回执后标记完成确认。双方各自的文件可同时进行该流程；重复或迟到的FINISH不回退状态。只收到ACK不能证明文件已交付。

`result.json`显式区分`connection_ready`（可接收）、`send_ready`（对端已就绪，可发送）、`receive_complete`、`transport.sent_confirmed`和`transport.receive_finish_confirmed`。未收到数据时received=null，receive_complete=false；未选择发送文件时sent_confirmed=null。status=complete要求连接就绪、已开始接收的文件完成并收到FINISH回执、可选发送文件被对端完整确认。持续服务到--seconds结束，以便对端补收控制消息；并非完成后立即退出。到期未满足条件则failed，保留部分统计。新UUID会清空在途、ACK和FINISH，重新发送本端文件；已完成文件与旧transport/sent摘要按peer UUID保留，不跨会话续传。

`sent.json`保存发送源身份，`received.json`保存本端交付，`data.jsonl`记录分片索引/尝试次数及拒绝/注入事件。每进程最多保留64张选定控制原图、8张选定业务原图和各会话标定原图，不表示保存全部光学帧。

仅实验配置可设置`test_faults: {"drop_data_once":[0],"drop_control_once":["ACK","FINISH"]}`。接收入口在USB解码之后丢弃指定索引的首个完整显示（同一持有画面的重复采集均丢弃），控制按首个指定kind的完整相同封装丢弃，遇到另一封装后恢复。数据按会话/配置/文件/索引各丢一次，控制按进程每kind一次。注入写入data.jsonl；它是软件接收丢弃，不能描述为物理线缆丢帧率。正常配置不启用注入。

本机可选send_fps_limit为有限正数；connect的发送及ACK自适应上限取它与视频协商FPS的较小值，默认不限制。它只降低本机发送节奏，不改变CAPS、配置ID、对端额度或协商上限，可用于同一会话/设备的受控负载观测。

磁盘原子发布要求暂存文件和目标位于支持硬链接的同一文件系统。不支持时明确失败并保留暂存文件，不回退为可见的部分received.bin；本轮验证只覆盖Windows/Pi实际输出目录。元数据仍随分片数增长，载荷本体不整文件驻留内存。

发布期间发现对端新身份时，先等待旧发布任务完成；成功后归档旧文件与收据，再以新会话接收，旧交付不向新会话发送FINISH。重启归档使用不覆盖的硬链接后unlink；同名归档已存在时会话失败，保留已有归档及尚未移走的源文件。各文件归档独立完成，不保证多文件一起提交。发布任务失败则传播异常并回收资源，保留part；本地已发布不等于FINISH双向确认。
