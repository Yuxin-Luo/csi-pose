# csi-pose 复现教程（大学生版）

> 项目地址：`d:\LYX\SONY_USTB\Wifi_CSI\csi-pose`
> 写给普通大学生：默认你懂一点 Python 和 Arduino，会装软件，但不熟悉 ESP-IDF / 嵌入式 / WiFi CSI。遇到名词就先看"大白话"那一栏，不要死磕。

---

## 0. 先花 5 分钟看明白这个项目到底在干啥

`csi-pose` 是开源仓库 **geekfeiw/WiSPPN** 的 ESP32 重制版：用 6 块 ESP32-S3 开发板（3 块发射 + 3 块接收）做"WiFi 雷达"，**只用 WiFi 信号**就能估计出房间里一个人的 18 个关节点 2D 坐标，再在上面套一个摔倒检测。摄像头只在录数据时当"老师"，给模型贴标签；**推理时完全不需要摄像头**。

它的 5 个阶段对应仓库的 5 个目录：

| 阶段 | 目录 | 大白话 |
|---|---|---|
| ① 固件 | `firmware/tx`、`firmware/rx` | 烧到 ESP32-S3 上的 C 代码。TX 板每秒广播 100 次小包，RX 板记录每次接收的"信道状态信息（CSI）"——也就是 2.4 GHz 电磁波被人挡住后的指纹。 |
| ② 主机 | `host/bridge`、`host/recorder`、`host/csi_pipe` | 电脑上的 Python 程序。`bridge.py` 把 3 块 RX 板的 USB 串口数据收下来并转发到 MQTT；`recorder.py` 把 MQTT 数据写到 HDF5 文件；`csi_pipe/` 是时钟对齐、采样切窗的工具库。 |
| ③ 老师标签 | `teacher/teacher.py` | 录像里跑 RTMDet+RTMPose（两个现成的人体姿态识别模型），把每帧的 18 个关节坐标写到 HDF5 里当"标准答案"。 |
| ④ 训练 | `train/train.py` | 用第 ③ 步的标准答案当监督信号，训练一个 ResNet-18 风格的小网络（`WiSPPN-ESP`），让它学会"看到 50 ms 的 CSI 张量就能预测关节坐标"。 |
| ⑤ 实时推理 | `rt/demo.py` | 加载训练好的权重，接收实时 CSI 流，每秒约 20 帧画骨架图，并用规则判断有没有摔倒。 |

每一阶段都是**独立的**——你可以只跑其中一段做实验。下面按阶段给一份**最低可行的复现路径**，最后再列常见报错。

---

## 1. 准备阶段：硬件 + 系统 + 基础软件

### 1.1 硬件清单（按 README §Hardware）

> 难度 ★★☆ — 不买齐就只能跑软件仿真（看 §1.4 兜底方案）。

- **6 块 ESP32-S3 开发板**（注意：是 S3，不是 S2 / C3）。其中 3 块刷 TX 固件当发射，3 块刷 RX 固件当接收。
- **6 根 USB 数据线**（Type-C / Micro-USB 看板子背面丝印），最好带数据传输功能（不是只能充电的那种）。
- **1 个 USB 摄像头**（录数据用，推理不需要；便宜 720p 的就行）。
- **一台能跑 Windows 10/11 的电脑**（项目作者推荐 Windows 主机 + WSL2 子系统组合）。
- **1 个 MQTT broker**：开源免费的 Mosquitto。
- 房间建议 3.45 m × 5.65 m 左右（作者实测的房间尺寸），中间放一张瑜伽垫用来做坐/躺/摔倒动作。

> 💰 淘宝/拼多多搜"ESP32-S3 开发板"，单块约 25–50 元；6 块 + 线 + 摄像头 + 排插预算约 300–500 元。

### 1.2 操作系统

- **推荐**：Windows 10/11 主机 + WSL2（Ubuntu 22.04 LTS）。
- 理由：仓库 README §Hardware 明确说"capture on native Windows, training on WSL2 (ext4)"。Windows 拿串口稳定，WSL2 ext4 文件系统做训练 I/O 快。
- 不要的：纯 macOS（USB 串口驱动有坑）、纯 Linux 桌面（没有 Windows 串口驱动稳，但也能跑）。

### 1.3 软件依赖一次装齐

打开 **WSL2 Ubuntu 终端**（PowerShell 里 `wsl` 回车），执行：

```bash
# 1) Python ≥3.10，pip
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git mosquitto

# 2) ESP-IDF v5.x（固件编译用，作者用的是 v5）
#    按官方文档装：https://docs.espressif.com/projects/esp-idf/zh_CN/latest/esp32s3/get-started/
#    装完后每次开终端都要 source 一下环境：
source ~/esp/esp-idf/export.sh

# 3) 把项目代码 clone 下来（或者你已经放在 D 盘了——WSL 里路径是 /mnt/d/...）
cd /mnt/d/LYX/SONY_USTB/Wifi_CSI/csi-pose

# 4) 装 Python 依赖
pip install -r requirements.txt
```

依赖一共 10 个：numpy / h5py / PyYAML / msgpack / paho-mqtt / pyserial / opencv-python / torch / onnxruntime / rtmlib。其中 `torch` 是大头，建议去 PyTorch 官网挑你显卡对应版本（NVIDIA 选 CUDA 版；没显卡就用 CPU 版）。

> ⚠️ Agent Rules 第 5 条：调用 API 时 RPM<200，TPM<10 000 000。这里指的是大模型 API，**不是**装包的速度限制，所以放心 `pip install`。

### 1.4 没硬件怎么办（兜底方案）

仓库的 `train/csi_train/` 训练代码是纯 Python + PyTorch，可以用**作者抓好的公开数据**训练（如果他 release 出来的话）。否则：

- 至少能跑通第 ④ 步的 **empty_session.py**（生成空样本做 pipeline smoke test），不需要硬件。
- 第 ⑤ 步的 `rt/demo.py --replay SESSION.h5` 也只要 HDF5 文件，不用硬件。

---

## 2. 第①阶段：烧固件（最容易翻车的环节）

> 难度 ★★★ — 嵌入式新手注意：6 块板要分别刷两种固件 + 编号，烧错就乱套。

### 2.1 给 6 块板标号（建议用便签贴）

| 编号 | 刷哪个固件 | 角色 | COM 口（Windows 设备管理器看） |
|---|---|---|---|
| TX0 | `firmware/tx` | 发射 0 号 | 待插上电脑查 |
| TX1 | `firmware/tx` | 发射 1 号 | 待插上电脑查 |
| TX2 | `firmware/tx` | 发射 2 号 | 待插上电脑查 |
| RX0 | `firmware/rx` | 接收 0 号 | 待插上电脑查 |
| RX1 | `firmware/rx` | 接收 1 号 | 待插上电脑查 |
| RX2 | `firmware/rx` | 接收 2 号 | 待插上电脑查 |

> CH340 串口芯片没有序列号，**COM 号会跟着 USB 口变**。建议用 USB 延长线把每块板固定在某个 USB 口，插拔顺序保持一致，然后**实测**确认 `COMx ↔ 编号` 的对应关系（烧完固件会从串口打 MAC 地址，可以据此对应）。

### 2.2 编译并烧 TX 固件

```bash
cd firmware/tx
idf.py set-target esp32s3        # 选芯片型号（只需一次）
idf.py build                     # 编译
idf.py -p COM3 flash monitor     # 烧录到 TX0（COM3 换成你那块板对应的口）
```

烧完后在 monitor 里按 Enter，应能看到：

```
BOOT role=tx idx=-1 mac=AA:BB:CC:DD:EE:01 ch=6 boot_id=42 fw=m0.1
```

记下 `mac=` 后面那串和 `boot_id`。再依次烧 TX1、TX2。

### 2.3 编译并烧 RX 固件

```bash
cd ../rx
idf.py set-target esp32s3
idf.py build
idf.py -p COM6 flash monitor     # 烧 RX0
```

烧完会打印 RX 板的 `BOOT role=rx mac=…`，记下 MAC。

### 2.4 给 TX 板分配 idx、设信道

TX 板默认 `idx=-1`、`ch=6`（在 `sdkconfig.defaults` 里改）。在 TX0 串口 monitor 里手动敲：

```
SET_IDX 0
SET_CH 6
HELLO         # 确认 idx=0
START rate=100
```

`rate=100` 表示每秒广播 100 次。TX1、TX2 同理，idx 分别设 1、2。

> README 里有个坑：3 块 TX 等周期广播会出现"相位对齐碰撞"，所以固件里加了 ±15% 随机抖动，**你不需要自己加**。

### 2.5 验证：3 块 RX 都能收到 3 块 TX 的包

随便挑一块 RX 板，先不开 TX 板，应该没有 `frame` 事件；依次开 TX0/TX1/TX2，RX 应该每秒收到约 100 × 3 = 300 帧。**如果收不到，先查 RX 板是否设了相同信道（CH），是否在 1–13 信道范围内。**

---

## 3. 第②阶段：主机采集（串口 → MQTT → HDF5）

> 难度 ★★☆ — Linux 上跑 Python，比较机械。

### 3.1 改配置：把 MAC / COM 填进 boards.yaml

```bash
cd /mnt/d/LYX/SONY_USTB/Wifi_CSI/csi-pose
cp configs/boards.example.yaml configs/boards.yaml
# 用 nano 或 VSCode 编辑 configs/boards.yaml，把每块板的 com 和 mac 填进去
```

填完后长这样（**示例**）：

```yaml
boards:
  - {id: tx0, role: tx, idx: 0, com: COM3, mac: "AA:BB:CC:DD:EE:01"}
  - {id: tx1, role: tx, idx: 1, com: COM4, mac: "AA:BB:CC:DD:EE:02"}
  - {id: tx2, role: tx, idx: 2, com: COM5, mac: "AA:BB:CC:DD:EE:03"}
  - {id: rx0, role: rx, idx: 0, com: COM6, mac: "AA:BB:CC:DD:EE:04"}
  - {id: rx1, role: rx, idx: 1, com: COM7, mac: "AA:BB:CC:DD:EE:05"}
  - {id: rx2, role: rx, idx: 2, com: COM8, mac: "AA:BB:CC:DD:EE:06"}
camera: {id: cam0, mount: "RX 阵列后方 0.65~0.95m", height_cm: 150}
```

> ⚠️ COM 口是 **Windows 视角**。如果 bridge 在 WSL 跑，需要 WSL 有串口访问（Win11 默认支持；Win10 要装 `usbipd-win`）。**更简单的做法是在 Windows PowerShell 里跑 `host/bridge/bridge.py` 和 `host/recorder/recorder.py`，把生成的 HDF5 拷到 WSL 文件系统（`\\wsl$\Ubuntu\...`）再做训练**——README §Hardware 的官方推荐路径。

### 3.2 起 MQTT broker（Windows PowerShell 管理员）

```powershell
# 方法 A：直接跑 mosquitto
mosquitto -c mosquitto.conf -v

# 方法 B：用 docker
docker run -it -p 1883:1883 eclipse-mosquitto
```

保持这个窗口别关。

### 3.3 起 3 个 bridge 进程（每个 RX 一份）

**每个 RX 板**开一个 PowerShell 窗口：

```powershell
cd D:\LYX\SONY_USTB\Wifi_CSI\csi-pose
python host/bridge/bridge.py --port COM6 --rx-id 0 --auto-start --raw-dir logs
python host/bridge/bridge.py --port COM7 --rx-id 1 --auto-start --raw-dir logs
python host/bridge/bridge.py --port COM8 --rx-id 2 --auto-start --raw-dir logs
```

窗口里会每秒打印 `[rx0] frames=… crc_drops=…`。`crc_drops` 一直涨的话说明 USB 线质量差或电脑 CPU 忙。

> README §Capture rules：**录制时不要开任何大程序**（Chrome、IDE 索引、杀软扫描），CPU 一卡串口就掉帧。

### 3.4 起摄像头采集（教师录制）

再开一个 PowerShell：

```powershell
python host/capture/cam_capture.py --out host/sessions --session s01-r1 --duration 780
```

`--duration 780` 是 13 分钟，和 README §Data-collection protocol 的 `m15-cap1` 一致。

### 3.5 起 recorder，把 MQTT → HDF5

再开一个 PowerShell：

```powershell
python host/recorder/recorder.py --out host/sessions --session s01-r1 --duration 780
```

跑完后你会得到：

- `host/sessions/s01-r1-20260707-xxxxxx.h5`（CSI 数据）
- `host/sessions/s01-r1-20260707-xxxxxx.mp4`（同步视频）
- `host/logs/rx0-20260707-xxxxxx.rawlog` 等（串口原日志）

### 3.6 录制流程：照着 README §Data-collection protocol 走

> 难度 ★★★ — 13 个段落，每个用 **Enter** 标记起止。

总共 13 段，作者实测 13 分钟。具体动作：

| 段 | 内容 | 时长 |
|---|---|---|
| 1 | 空房间 | 60 s |
| 2–10 | 在 3×3 网格的 9 个位置各站 40 s，每个位置依次面朝 N/E/S/W 各约 10 s | 6 min |
| 11 | 中间垫子上坐下 | 40 s |
| 12 | 躺下（头朝北） | 60 s |
| 13 | 空房间（人离开） | 60 s |

> ⚠️ README 警告：**录制中间千万别 Ctrl-C**（会废掉当前段）；录完 13 段后 logger 会自动关闭。

### 3.7 rawlog → HDF5 重建（可选）

如果你中间 bridge 重启过，rawlog 会切成多段。重建：

```bash
python host/tools/rawlog_to_hdf5.py --raw-dir host/logs --out host/sessions/s01-rebuild.h5
```

它走的是 `csi_pipe/rebuild.py`，把串口原日志重新解包成 HDF5。

---

## 4. 第③阶段：跑老师模型，生成关节标签

> 难度 ★★☆ — 一行命令搞定，但要下模型（首次约 200 MB）。

```bash
cd teacher
python teacher.py label ../host/sessions/s01-r1-xxxxxx.mp4 --h5 ../host/sessions/s01-r1-xxxxxx.h5
```

跑起来后：

1. 首次会从 `download.openmmlab.com` 自动下 RTMDet-m 和 RTMPose-m 的 ONNX（**网络要稳**，若失败可手动下放缓存目录）。
2. 处理完会把 18 个关节点坐标 + 状态码（ok / no_person / multi）写到 HDF5 的 `/labels` 分组。
3. 终端会显示 fps 和三类帧的统计。

### QA 抽检（建议做）

```bash
python teacher.py qa --h5 ../host/sessions/s01-r1-xxxxxx.h5 --out qa_s01 --sample 200
```

会生成一个 HTML 相册（每张图叠骨架）。**用浏览器打开，按 `o` / `x` 标"对 / 错"→ 点页面里的"JSON 내보내기"按钮导出判别结果**。README 说正常情况下 fail 率应 < 2%。

```bash
python teacher.py gate qa_s01/verdicts.json
```

如果显示 `PASS`，再把 QA 判别结果注入训练样本：

```bash
python teacher.py pam --h5 ../host/sessions/s01-r1-xxxxxx.h5 --verdicts qa_s01/verdicts.json
```

---

## 5. 第④阶段：训练 WiSPPN-ESP

> 难度 ★★★ — 唯一需要 GPU 的环节。

### 5.1 写 train.yaml

```bash
cd /mnt/d/LYX/SONY_USTB/Wifi_CSI/csi-pose
cp configs/train.example.yaml configs/train.yaml
```

把 `sessions` 里 h5 路径换成你的：

```yaml
sessions:
  - {h5: /mnt/d/LYX/SONY_USTB/Wifi_CSI/csi-pose/host/sessions/s01-r1-xxxxxx.h5, role: train, type: example-t80}
  - {h5: /mnt/d/LYX/SONY_USTB/Wifi_CSI/csi-pose/host/sessions/s01-r1-xxxxxx.h5, role: val,   type: example-v20}
hyper: {batch: 64, epochs: 30, warmup: 2, lr: 1.0e-3, wd: 1.0e-4, knn_k: 5, seed: 0}
```

> 单 session 没真正 val，按时间切 80/20 也行：用 `train/split_session.py`。

### 5.2 跑训练

```bash
python train/train.py fit --config configs/train.yaml --loss-mode pam_full
```

可选：
- `--rssi`：把每条链路的 RSSI 当辅助特征（M2.5 改进）。
- `--phase`：把清洗后的相位当第 560 通道拼进去。
- `--augment`：开 GPU 数据增强（4 种）。
- `--vector-head`：把头部换成直接回归 18×2 坐标的小头（仅 `loss-mode diag_only`）。
- `--compile`：用 `torch.compile` 加速（PyTorch ≥ 2.0）。

权重会落在 `runs/<name>/best.pt`。

### 5.3 评估 + 基准对照

```bash
# 在 val 上跑一遍 PCK@0.2 / PCK@0.5
python train/train.py eval --ckpt runs/<name>/best.pt --config configs/train.yaml

# 对照 mean-pose / kNN 等简易 baseline（确认你的模型比 baseline 强）
python train/train.py baselines --config configs/train.yaml
```

README §Results 给的参考：PCK@0.2 ≈ 0.495 / PCK@0.5 ≈ 0.897（**单 session、单人、单房间**，仅参考）。

### 5.4 进阶：M2.5 消融

```bash
python train/run_ablation.py   # 跑 M2 的输入表示消融
python train/run_m25.py        # 跑 M2.5 的 phase / rssi 3 跑消融
```

`Agent Rules` 第 10 条：只做小规模验证性测试，所以 **epochs 改 3–5 跑通就行**，别一上来 30 epoch 烧几天。

---

## 6. 第⑤阶段：实时推理 + 摔倒检测

### 6.1 回放模式（不需要硬件，最容易验证）

```bash
python rt/demo.py --replay host/sessions/s01-r1-xxxxxx.h5 --ckpt runs/<name>/best.pt
```

会弹出窗口：左边是骨架图（由 CSI 推理），右边是录像（可选 `--video`），顶上横幅显示 `IDLE / IMPACT / ALARM`。按 `q` 或 `ESC` 退出。

跑完后会写一份性能报告到 `host/logs/rt_perf.json`。

### 6.2 实时模式（需要硬件 + MQTT）

确认所有 bridge 还在跑、MQTT 在。然后：

```bash
python rt/demo.py --live --ckpt runs/<name>/best.pt --config configs/rt.yaml
```

> 配置文件 `configs/rt-live-relay.yaml` 是 WSL 中继路径用的（`settle_ms=200` 代替 30），先试 `rt.yaml`，出问题再切。

### 6.3 摔倒检测阈值（README 明确说"暂定"）

`configs/rt.yaml` 里所有 `theta_*`、`aspect_*` 都是用单 session `fall-demo-01` 标定的，**不要在另一个房间照搬**。重新校准的最简单做法：

1. 在新房间录几段：10 次真实摔倒 + 10 次日常动作（弯腰、坐下、躺下睡觉）。
2. 看 `host/logs/rt_perf.json` 的 `alarms` 和落点是否对得上真值。
3. 调 `theta_v`（髋部下沉斜率阈值）、`aspect_hi`/`aspect_lo`（直立/躺下判定）三组参数。

---

## 7. 完整复现路线图（按时间顺序）

| 步骤 | 做什么 | 大概耗时 | 需要的硬件 | 风险 |
|---|---|---|---|---|
| 0 | 装 ESP-IDF + Python 依赖 | 1–3 h | 无 | 编译 ESP-IDF 容易失败 |
| 1 | 烧 6 块板固件 + 编号 | 1–2 h | 6× ESP32-S3 | CH340 COM 口漂移 |
| 2 | 第一次录制（13 段、13 min） | 0.5 h | + USB 摄像头 | 录制中 Ctrl-C 全废 |
| 3 | 老师打标签 + QA | 0.5 h | 无（要 GPU） | 失败率 > 2% 就要重录 |
| 4 | 训练（30 epoch） | 2–8 h | GPU | 没 GPU 改 CPU 跑 30+ h |
| 5 | 实时推理 + 摔倒调参 | 1 h | 全部 | 阈值标定不通用 |

**最短路径**（不录数据，只验通 pipeline）：**跳到第 ④ 步的 `empty_session.py` 生成一份空 HDF5**，跑训练几个 epoch 看 loss 下降。

---

## 8. 常见报错与排查（FAQ）

> 难度 ★ — 90% 的问题都集中在下面几条。

### 8.1 烧固件时报 `A fatal error occurred: Failed to connect to ESP32-S3`

- 板子进入下载模式了吗？**烧 RX 板时按住 BOOT 按钮再插 USB**（部分板子需要），或者板子上有自动下载电路就不用按。
- USB 线是不是只能充电？换一根。
- 驱动装了没？S3 用 ESP-IDF 自带的 USB Serial/JTAG 驱动，旧板用 CH340 驱动。

### 8.2 bridge 一直 `crc_drops` 涨

- USB 线质量差 → 换带屏蔽的短线。
- 电脑 CPU 忙 → 录的时候关掉 Chrome、Slack、杀软。
- COM 缓冲不够 → `bridge.py` 已经做了 Windows 256KB 缓冲设置，非 Windows 忽略。

### 8.3 recorder 几分钟后无帧写入

看 stderr：是不是 MQTT 断了？桥接脚本里有自动重连但需要时间。**最稳的做法是直接重跑**——因为 rawlog 是原日志，bridge 死了 recorder 死了**都不会丢数据**，事后用 `rawlog_to_hdf5.py` 重建。

### 8.4 teacher 标签失败率 > 5%

- 摄像头是否太远 / 太偏？调到能拍到全身。
- 室内光线够不够？RTMDet 在弱光下会丢检测。
- 看 HTML 抽检相册：是漏检（无人框）还是错位（关节飘到背景）？

### 8.5 训练时 CUDA out of memory

- 把 `configs/train.yaml` 里 `batch` 从 64 改到 16 / 8。
- 关掉 `--augment`（会临时放大 4 倍）。
- 改用 `--vector-head --loss-mode diag_only`（小得多的输出头）。

### 8.6 训练 loss 不下降 / NaN

- 检查 HDF5 的 `/samples/presence` 分布：是不是全 0 或全 1？
- 把 lr 从 `1e-3` 降到 `1e-4`。
- 数据太短（< 5 min）模型学不到东西；README 单 session 也只跑 13 min。

### 8.7 实时推理窗口全是掉帧（dropped > 50%）

- `settle_ms` 太短：USB 串口有 10–30 ms 批处理延迟。先看 `host/logs/rt_perf.json` 的 `e2e_ms_p95`，> 100 ms 就把 `rt.yaml` 的 `settle_ms` 加大到 100。
- WSL 中继路径务必用 `rt-live-relay.yaml`（`settle_ms=200`）。

### 8.8 Agent Rules 第 8 条相关：连续 5 个报错

如果同一个错误反复出现 5 次，**先停手**，把以下信息写到 `dev-doc/` 下做份 debug 报告：

- 报错截图 / 完整 traceback。
- 你已经试过的 3 种修法。
- 你怀疑是环境问题还是代码问题的判断。

再回来告诉我，我帮你决定是换路径还是绕过去。

---

## 9. 给大学生的时间建议

| 你有多少时间 | 建议 |
|---|---|
| 周末 1 天 | 只跑通第 ① 步烧固件 + 第 ⑤ 步回放（用作者 sample h5） |
| 1 周 | 加上第 ② ③ 步，自己录一段 13 min 数据 |
| 2 周 | 加上第 ④ 步训练，把 PCK@0.2 跑到 > 0.4 |
| 1 个月 | 做跨 session 验证 + 摔倒阈值重标定（这才是能写进简历的） |

---

## 10. 参考资料

按 Agent Rules 第 7 条，我维护一份参考资料清单。原始论文、仓库、关键文档都在这里：

| 主题 | 链接 / 路径 |
|---|---|
| 原始论文 WiSPPN | https://arxiv.org/abs/1904.00277 |
| 原始仓库 | https://github.com/geekfeiw/WiSPPN |
| 本仓库 README | `README.md` / `README.ko.md` |
| 技术栈详解 HTML | `docs/csi-pose-techstack.html` |
| RTMPose 模型 | https://github.com/open-mmlab/mmpose（Apache-2.0） |
| ESP-IDF 中文文档 | https://docs.espressif.com/projects/esp-idf/zh_CN/latest/esp32s3/get-started/ |
| 数据集收集协议 | README §Data-collection protocol |
| 时钟对齐数学 | README §Key idea 1 + §Mathematical formulation |
| 模型架构图 | `figures/fig_model_en.png` |
| 摔倒状态机 | `figures/fig_fsm_en.png` + README §What it detects |

---

## 11. 自检清单（每完成一段打 ✓）

- [ ] ESP-IDF 装好，`idf.py --version` 能跑
- [ ] 6 块板都烧好固件并标号，串口 `HELLO` 能回 MAC
- [ ] `boards.yaml` 填好 COM ↔ MAC
- [ ] Mosquitto 在跑
- [ ] 3 个 bridge 进程都在跑、`crc_drops` 缓慢增长
- [ ] recorder 写出的 h5 文件 size 在涨
- [ ] teacher 标签跑完、`ok` 帧 > 70%
- [ ] QA fail < 2%（否则重录）
- [ ] 训练 loss 下降、`runs/.../best.pt` 存在
- [ ] `train/train.py eval` 跑通 PCK
- [ ] `rt/demo.py --replay` 弹窗显示骨架
- [ ] （可选）`rt/demo.py --live` 实时画骨架 + 摔倒横幅

---

写完收工。这份教程假设你按顺序做、且至少跑通第 ① + 第 ⑤ 步回放两段再尝试别的。**遇到卡点先看 §8 的 FAQ，没答案再问**，别在一个点上死磕一下午——这项目最常见的失败模式是"硬件时好时坏 + 录制条件不一致"，而不是代码 bug。