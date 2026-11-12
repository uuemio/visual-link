# 实拍诊断

修复前短连接 `diagnosis-A-*`：Windows capabilities 记录当前1920×1200，Pi当前1920×1080。Pi首次接收 `diagnosis-A-pi/startup/stage-000/first-control.png` 为1920×1080；请求、驱动报告及实际尺寸均为1080p。完整控制框可见，垂直被压缩，并非黑屏或裁切。原decode报 no valid bootstrap control frame，round=0，双方未完成能力交换。

Pi原部署automatic.py及SHA见 pi-original-automatic.py、deployment.json；原失败会话见prior-pi-failure。诊断只在Pi接收处临时记录首帧，未先修改识别行为。

方案：启动接收独立选择USB采集尺寸；有有效输入时序则优先使用，否则依次尝试1080p/1200p，每4秒无有效控制则重开。控制矩形按横纵独立归一化，保留RS、头部、CRC验证。选档后沿用原refresh_capture、标定及业务路径。

Pi接口VIDIOC_QUERY_DV_TIMINGS返回255 / Inappropriate ioctl for device，不能将USB current_output当作HDMI输入时序；本机实测走候选采集模式路径。Windows实测信号source/active均为1920×1200、scaling=1。
