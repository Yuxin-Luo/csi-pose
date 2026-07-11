# 14 — End-to-End Handoff (数据采集 → 训练 → 实时推理)

**状态**：✅ Handoff 文档（2026-07-11 23:45）
**目的**：让下一个 agent 顺利接手剩下的 3 个 phase：数据采集 norm 长跑、模型训练 baseline、实时 inference demo。
**总工作量估计**：~2-4 小时 per phase。

---

## 1. 项目一段话

csi-pose: 用 **6 块 ESP32-S3** 做 **3TX + 3RX** WiFi CSI 2D 人体姿态估计 + 规则式跌倒 FSM 检测。
WiSPPN（Intel 5300 单 NIC 3×3 antenna matrix）思路被移植到 ESP32-S3 的 **3×3 link matrix**。
**训练范式**：webcam+RTMPose 当教师打伪标签，CSI 学回归 PAM；**推理时无需摄像头**。
来源：父项目 `ESP32_FallRec_Reference/CLAUDE.md` + `csi-pose/CLAUDE.md`。

---

## 2. 当前位置（end of Phase 1 部分）

| Phase | 状态 | 哪步 |
|---|---|---|
| Phase 1a：test mode 60s smoke | ✅ 完成 | 5 次 run 都过——mp4/h5/rawlog 三件套齐全，cam fps 27-31，链路 0-0 loss 1.62% |
| Phase 1b：norm mode 580s 长跑 | ⏸ **下一步** | `./host/boot_recording.sh norm s01-r1`（用户主持） |
| Phase 2：train baseline | 📋 设计已完成 | 跑 `fall-demo-01` 上的 `train/train.py` baseline (dev_doc/5 已有 数据) |
| Phase 3：rt/demo.py 实时 | 📋 设计已完成 | 需要等 Phase 2 模型跑通 |

**正在等用户决定下一步**（让出 handoff，停一会儿）。

---

## 3. 环境 / 路径

```bash
# Anaconda env（必须用此 python，否则 cv2 / msgpack 装在不同 env）
PYTHON=/home/ruo/anaconda3/envs/dac_dev/bin/python

# 6 块 ESP32：
#   TX0/1/2  ← power bank（不接 console, 没有 USB 调试）
#   RX0/1/2  ← USB → /dev/ttyACM0/1/2
# cam 在 /dev/video0
# mosquitto 在 127.0.0.1:1883（boot.sh 自动启动）

# 项目 root（必须）
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose

# Workspace 产出路径
data/test/<session>-*.h5   ← test mode hdf5
data/test/<session>-*.mp4  ← test mode video
logs/test/rx*.rawlog        ← test mode rawlog
data/<session>-*.h5         ← norm mode hdf5
data/<session>-*.mp4        ← norm mode video
logs/rx*.rawlog             ← norm mode rawlog
logs/boot-<session>-<mode>-<ts>/live.log cam.log recorder.log boot.trace
```

---

## 4. 关键命令 cheatsheet

### 4.1 数据采集

```bash
# 快速 smoke (60s, 4 segments)
./host/boot_recording.sh test s01-smoke         # 默认可改 session 名

# 真实长跑 (580s, 13 segments)
./host/boot_recording.sh norm s01-r1

# 自定义 session 名 + plan
./host/boot_recording.sh test my-fast-bench 60

# Legacy 用法 (旧 ./boot_recording.sh s01-r2 现在报 usage —— 必须显式 mode)
```

### 4.2 工具

```bash
# Cam fps 矩阵测试 (MJPG/YUYV x 640/720/1080)
python host/tools/probe_fps.py --fourcc MJPG --width 640 --height 360

# USB snapshot pre/post
./host/tools/snapshot_usb.sh pre        # 录前
./host/boot_recording.sh test ...
./host/tools/snapshot_usb.sh post       # 录后 + diff

# rawlog 序列号 gap 分布（dev_doc/11 §1.1 模板）
python -c "
import sys, struct
sys.path.insert(0, 'host')
from csi_host.rawlog import read_rawlog
recs = list(read_rawlog('logs/test/rx0-20260711-232516.rawlog'))
seqs = [...]
# 计算 gap 分布
"
```

### 4.3 验收 h5

```python
import h5py, json, numpy as np
f = h5py.File('data/test/...h5', 'r')
print(list(f.keys()))                    # ['links','meta','video']
print(f['/links'].keys())                # 9 链路 × (rx,tx)
print(f['/meta'].attrs.keys())
rs = json.loads(f['/meta'].attrs['recorder_status'])
for k, v in rs['links'].items():
    print(f"{k}: rx={v['rx']} lost={v['lost']} loss={v['loss']*100:.2f}%")
```

---

## 5. Phase 1b —— Norm 长跑（推荐先做）

**目标**：580s 13 段 D1 plan（dev_doc/6 §4）+ 真实 loss baseline 验证。

### 5.1 command

```bash
# 在 project root 普通 user（dac_dev）env 启动
./host/boot_recording.sh norm s01-r1
```

### 5.2 流程

1. preflight（检查 mosquitto、ttyACM0/1/2、video0）
2. polling 等 3 bridge frames > 280（≈2s）
3. cam 弹窗 — 按任意键启动
4. 580s 录制，期间按 plan 自动段切换（overlay 右上角）
5. 自动结束 → 7 产物：1 h5 + 1 mp4 + 3 rawlog + boot log dir

### 5.3 验收 checklist

打开 h5 验证：
```bash
python host/tools/accept_recording.py data/s01-r1-*.h5   # （待写）
```

或者手验：
- h5 `/links/.*/iq` shape = (N_frames, 56, 2)
- h5 `/links/.*/t_ns` 跨度 ≈ 65 min (580s + 启动/结束偏移)
- h5 `/video/t_ns` 跨度 ≈ 580s
- h5 `recorder_status.links.*.loss` 平均 ≈ 4.5%
- mp4 frames ≈ 17500（580s × 30fps）
- rawlog 3 块 RX 各 ≥ 2.5MB

### 5.4 已知陷阱

- **必须按 cam 弹窗里的任意键**（Enter / Space 数字 都行）—— 主 terminal 不需要按
- **如果 cam 不弹窗**：`dispaly env` 可能缺失。本机有 X11 的话 cv2 会用，否则需要 Xvfb wrapper（dev_doc 不考虑远程 WSL）
- **recorder 没启动** = mosquitto 没起；boot.sh 会自动 `mosquitto -d -p 1883`，但需要 `apt install mosquitto` 在 system 上
- **TX 板子已持续运行** —— 不需要重启 TX 板；brige.py 会从 high seq 处 baseline

---

## 6. Phase 2 —— 模型训练 baseline

### 6.1 目标

把 s01-r1 h5 + (未来) teacher labels 喂进 `train/train.py`，重现 WiSPPN-ESP 在 ESP32 CSI 上的姿态回归。

### 6.2 数据准备（如果 Phase 1b 完成）

- s01-r1 h5 是基础；**还需要** webcam video (mp4) → RTMPose 标签 (teacher output)
- teacher/teacher.py 已经实现 label flow:
  ```
  python teacher/teacher.py label <mp4> --h5 <h5>     # 打姿态标签写到 h5:/labels
  python teacher/teacher.py qa    --h5 <h5> --out qa/  # QA gallery
  python teacher/teacher.py pam   --h5 <h5> --verdicts  # 生成 PAM 训练输入
  ```

### 6.3 训练入口

```bash
python train/train.py --h5 data/s01-r1-*.h5 \
                      --split train/splits.json \
                      --out train/ckpt/s01-r1/
```

详见 `train/train.py` argparse + dev_doc/5 §3 (loss baseline 已有 4.13-4.53% acceptance threshold)。

### 6.4 已知 issues

- `train/` 的实现我没碰过；handoff 时**先读 README 然后 pilot 跑 5 epochs 估时**
- teacher 标签可能要 WSL2 环境（RTMPose 装在 conda 上）—— 这台机是 native Linux，应该 OK
- PAM 转换 + 训练 **全 pipeline 可能要 1-2 小时单 GPU**

---

## 7. Phase 3 —— rt/demo.py 实时推理

### 7.1 目标

20Hz 实时姿态估计 + 规则式跌倒 FSM demo。

### 7.2 启动

```bash
# 在已训好模型后：
python rt/demo.py --ckpt train/ckpt/s01-r1/best.pth \
                   --h5 data/s01-r1-*.h5 \
                   --config configs/rt.yaml
```

### 7.3 跌倒 FSM 实现

`rt/csi_rt/`（dev_doc 不深查，但 commit 时点为 focused source）：
- IDLE → IMPACT → ALARM 三态
- ≥2/3 cues 触发 IMPACT (R1 骨盆快速下降 + R2 站→躺 + R3 头部掉到下半屏)
- ALARM 需 hold 窗口

详见 csi-pose README §3 关键设计 + 父 CLAUDE.md §11 红线 #2 不可直接套阈值。

---

## 8. Known Gotchas / 不踩同样的坑

### 8.1 不要重用 `rawframes` 当 CSI count

bridge.py `rawframes` 是 USB CDC byte count，不是 CSI frame count。dev_doc/9 v4 误读纠正。

### 8.2 不要相信 live.log `loss > X%`

live.log 累计 `LinkTracker`，**永远显示 95% loss**（dev_doc/5 §3.2 startup cumulative）。要信 **h5 `recorder_status.links.*.loss`**。

### 8.3 不要直接套 cam_capture mp4 metadata 的 fps

cv2.VideoWriter 用 `CAP_PROP_FPS`=30 写 metadata，但 USB2 cam 实际只跑 15-27fps。从 commit `a7f10e7` 起，cam_capture 加了 **30 帧 grab burst calibration** 把真实 fps 写进 mp4 metadata。

### 8.4 不要假设 cam @ 720p 真 30fps

经 probe 验证（dev_doc/12）：**MJPG 640x360 是唯一真 30fps**；720p/1080p 全是 15fps 或更低。

### 8.5 不要绕开 USB autosuspend

如果 loss 突增（4.5% → 15%），先 `echo on > /sys/bus/usb/devices/1-{1,6,10}/power/control`（需要 sudo）。dev_doc/11 §5 已记。

### 8.6 不要用 `bridge.py` 自己的 `time.strftime` for rawlog 命名

从 commit `bb3fa08` 起，bridge.py 加 `--log-ts` 参数，boot.sh 显式注入统一 TS。否则 boot script 跟 rawlog 文件名 +1 秒错位。

### 8.7 Windows 路径用 COM 而非 /dev/ttyACM

`./host/boot_recording.sh` 在 WSL2 上需要改 USB 设备映射（dev_doc 没测过）。本机是 native Linux，路径 OK。

### 8.8 不要连续 patch 同一 bug 5 次

父 CLAUDE.md §9.1: 连续 5 报错必须**退出自动模式**，写 debug 报告，问人工。

---

## 9. 决策追溯（CLAUDE.md §3.3）

跨整个 session 的决策清单（commit hash 给出，方便查 diff）：

| 决策 | dev_doc | commit |
|---|---|---|
| TEST 60s 4 段 + 隔离子目录 | dev_doc/10 | 5c8f722 |
| Cam MJPG 640x360 | dev_doc/12 | 299cc30 |
| Cam fps calibration (30 帧 grab burst) | dev_doc/12 §6 | a7f10e7 |
| cv2.waitKey gate (替代 input()) | dev_doc/9 §6 | 91d09d3 |
| sentinel file sync cam → recorder | dev_doc/9 §6 | 91d09d3 |
| bridge.py --log-ts 统一 TS | (本文件 §8.6) | bb3fa08 |
| Disable USB autosuspend on 1-{1,6,10} | dev_doc/11 §5.2 | (root 手动) |

---

## 10. 给下一个 agent 的提示（来自现场经验）

1. **不要假设 boot 永远 work**：先 `./host/boot_recording.sh test` 跑一次 smoke 确认环境再上 norm
2. **不要连续 3 次修同一个 bug 不出来**：写 debug report，问用户
3. **不要相信 live.log 上 loss 大就以为数据坏了**：去查 h5 `recorder_status.links.*.loss`
4. **不要相信 CAP_PROP_FPS**：用 timing 实测（probe_fps.py 模板）
5. **不要试图改 rawlog encoding**：rawlog 是历史 dump，改 layout 会 break `read_rawlog`
6. **不要 root 强行 disable USB 自动 suspend 在用户机器上**：先问，**这是 system 改动**

---

## 11. 关键文件 + 行速查（按主题）

| 主题 | 文件 | 范围 |
|---|---|---|
| boot 编排 | `host/boot_recording.sh` | 105 行 |
| 桥接 | `host/bridge/bridge.py` | `main()` line 49-137 |
| CSI 帧解析 | `host/csi_host/framing.py` | `parse_frame` line 67 |
| Gap 跟踪 | `host/csi_host/gap.py` | `LinkTracker` line 1-34 |
| Rawlog 格式 | `host/csi_host/rawlog.py` | CSIRAW01 + record |
| Cam | `host/capture/cam_capture.py` | 259 行 |
| Plan segment | `host/capture/plan.py` | 67 行（pure funcs）|
| Recorder | `host/recorder/recorder.py` | `main()` line 22 |
| Teacher | `teacher/teacher.py` | cmd_label / qa / gate / pam |
| Train entry | `train/train.py` | （未碰） |
| RT entry | `rt/demo.py` | （未碰） |
| FSM impl | `rt/csi_rt/` | （未核对具体文件）|

---

## 12. Open Questions（给用户 / 下个 agent 决策）

- ❓ norm s01-r1 是不是用户自己跑 → 需要 user 主控（10 分钟录制）
- ❓ teacher.py 在这台机是否真能跑 RTMPose (需 `pip install rtmlib`)？
- ❓ 训练 GPU/内存是否到位（train/ 可能要 GPU）？
- ❓ rt/demo.py 是否需要额外 GUI 装饰（cv2.imshow 实时预览）？

---

**维护者**：Claude (session 2026-07-11 22:00–23:45)
**依据**：本 session 全部 commit + dev_doc/6..13 + 父项目 CLAUDE.md
**下次启动提示**：先 `./host/boot_recording.sh test` 跑一次，确认产物形状匹配；通过后转 norm
