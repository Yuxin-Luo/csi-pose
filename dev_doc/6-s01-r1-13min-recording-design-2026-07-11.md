# 设计 06 — s01-r1 首次录制（D1 plan：3 站 × 4 朝 × 3 组 + supine）

**状态**：⏸ **待用户复核**（按 brainstorming skill 第 6 步：用户审完才进 writing-plans）
**设计时间**：2026-07-11
**覆盖**：完成 §2.5（firmware + loss baseline 4-5%）后第一次进 §3 host 采集
**plan 选择**：D1 = 3 站位 × 4 朝向 × 3 组重复 + 1 坐 + 1 supine + 2 empty（用户物理空间：3 站 + 1 坐 + 1 趴点，趴点现场反向拍 supine）
**总时长**：580s 录制时间（墙钟 ~10.3min 含走路）
**范围**：仅本 dev_doc。teacher 标签 / 训练 / 推理 / README 不在本设计范围。

---

## 0. 上下文回顾（按 dev_doc/4 + 5 既有结论）

| 项 | 状态 | 依据 |
|---|---|---|
| §2.5 链路验收 | ✅ 通过 | dev_doc/5 §5：T4 综合 loss 4.13–4.53% / CRC=0 / Reboot=0 |
| TX 端 | 充电宝供电，**不上电复位** | 用户 2026-07-11 确认电量足够 |
| RX 端 | 3 块 ESP32-S3 USB Serial/JTAG 直连 PC | dev_doc/4 §3.4 sdkconfig 双切到 USB |
| 串口 | `/dev/ttyACM0/1/2` | 用户已实测 |
| 视频 | `/dev/video0` | 用户已实测 |
| 录制环境 | 纯 Linux + conda `dac_dev` 环境 | 用户 2026-07-11 确认 |
| h5py 3.14.0 / cv2 5.0.0 / numpy 2.0.2 | ✅ 已装 | 用户 2026-07-11 装好 |
| mosquitto | ✅ pid 1072/26190 在跑 | 系统层 |
| bridge.py / cam_capture.py | ✅ Linux 兼容（port 是字符串 / V4L2 backend） | code review |
| README §Data-collection protocol | 13 段 / 13min / m15-cap1 | dev_doc/2 §3.6 |

**为什么需要重新设计而不是直接照 dev_doc/2 §3**：原教程默认 **Windows + WSL2** 串口透传、CH340/CP2102 桥接。你的 setup 是 **ESP32-S3 原生 USB Serial/JTAG + 充电宝 TX**，三个差异点必须显式处理：

1. 直烧版 RX 固件无 console 命令解析 → bridge 的 `--auto-start` 不能用
2. cam_capture 默认 `--backend msmf` 是 Windows 后端 → 必须 `--backend any` 走 V4L2
3. TX 充电宝供电 13 分钟无监控 → 中途掉电补救路径必须前置

---

## 1. 设计目标

按 §3 README §Data-collection protocol **改写版（D1：3 站 × 4 朝 × 3 组 + supine + 2 empty）** 跑完首次 **s01-r1** 录制，产出：

- `data/s01-r1-202607XX-HHMMSS.h5` —— raw CSI（`/links/{rx}{tx}/{t_ns,iq,...}`）+ cam/meta（`/video/{t_ns,frame_idx}`），单链路 ~36k 帧
- `data/s01-r1-202607XX-HHMMSS.mp4` —— 720p mp4v，~17k 帧
- `logs/rx{0,1,2}-202607XX-HHMMSS.rawlog` —— 3 份原日志，~9k CSI 帧/份

**为什么是 D1 而不是 README 原版**：用户 2026-07-11 物理空间限制（3 站位 + 1 坐点 + 1 趴点，趴点现场反向拍 supine）。D1 = 3 站 × 4 朝 × 3 组 = 36 单元（与 README 9×4=36 cell 数对齐），但只覆盖 3 个位置而非 9 个。详细 D1 vs README 对照见 §4。

**验收（按 dev_doc/5 §3.3 chunked streaming 解析法）**：
- h5 `/links/00/t_ns` 第一维（即首个链路 CSI 帧数）≥ 36,000（按 T4 295fps × 580s × 0.8 有效 ≈ 137k / 9 link × 3 链路 ≈ 45k 的保守下限；`/samples/csi` 是 samples.py build artifact，**录完后才有**）
- mp4 frame count ∈ [15,500, 17,500]（按 580s × 30fps ≈ 17,400 ± 10%）
- 3 份 rawlog CSI 帧数极差 < 5%
- h5 `/video/frame_idx` 行数 / mp4 帧数 ∈ [0.95, 1.05]

---

## 2. Pre-flight（5 分钟，§1.1 跳过）

| # | 动作 | 命令 | 失败处理 |
|---|---|---|---|
| ~~1.1~~ | ~~装 h5py + cv2~~ | ~~已装（用户 2026-07-11）~~ | — |
| 1.2 | 填 `configs/boards.yaml` | `cp configs/boards.example.yaml configs/boards.yaml`；6 块板的 `com` 改 `/dev/ttyACM*`，mac 填刚读到的（大小写严格） | mac 不对会让 csi_pipe 按 MAC 找不到 link |
| 1.3 | 确认 mosquitto | `ss -tlnp \| grep 1883` 或 `pgrep mosquitto` | `mosquitto -d -p 1883` |
| 1.4 | 确认 3 RX 串口 | `for p in 0 1 2; do /home/ruo/anaconda3/envs/dac_dev/bin/python -c "import serial; s=serial.Serial('/dev/ttyACM$p', 921600, timeout=0.5); print('$p', s.read(64)); s.close()"; done` | 拔插；dmesg \| tail -20 |
| 1.5 | 房间布置 | 3×3 grid 标记（胶带）+ 中间瑜伽垫 + 摄像头在 RX 阵列后 0.65–0.95m | 1.5 必须**早于** §3 完成 |

> ⚠️ **TX 充电宝不再列为风险**（用户 2026-07-11 确认 10min 电量足够）。

---

## 3. 启动入口：单 boot 脚本（替代 5 个独立终端）

**数据流**（无变）：

```
3×RX ESP32-S3 → /dev/ttyACM{0,1,2} → 3× bridge.py ─┐
                                                   ├→ MQTT(1883) → recorder.py → data/s01-r1-*.h5
                                       logs/rx*.rawlog ┘
USB 摄像头 → /dev/video0 → cam_capture.py ─────────────────→ cam/meta topic → 同一 h5
```

**用户操作只有一行**：

```bash
./host/boot_recording.sh s01-r1
```

**脚本行为**（伪代码见 §6.3）：
1. 预检 mosquitto / 3 个 `/dev/ttyACM*` / dac_dev 环境
2. 后台启 3 个 bridge → 等所有 bridge frames > 280
3. 后台启 cam + recorder（两者都用 `--start-on-key`，**用户在终端按 Enter 才开始录**）
4. 等 cam/recorder `--duration 580` 到期自然退出
5. 给 3 个 bridge 发 SIGTERM
6. 打印产物清单（h5/mp4/rawlog 路径 + 大小）

**时间戳命名**（**cam_capture.py:162** 和 **recorder.py:42** 已自带）：
- `data/s01-r1-20260711-153045.h5`
- `data/s01-r1-20260711-153045.mp4`
- `logs/rx0-20260711-153045.rawlog` × 3 份
- 不撞名由 `time.strftime('%Y%m%d-%H%M%S')` 保证

**为什么用 boot 脚本不用 5 终端**（决策依据见 §7）：
- ✅ 一行命令启动，远程操控时不用切 5 个窗口
- ✅ 自动预检、自动清理、自动打印产物清单
- ✅ 子进程失败时主脚本短路（killall 子进程 → 报告失败步骤）
- ❌ 不能像 5 终端那样实时看每个 bridge 的 frames 数字（但 boot 脚本会轮询日志打印在主终端）

**5 个子进程启动命令**（脚本质上就是包了这些命令）：

```bash
# 终端 1 (bridge rx0)
/home/ruo/anaconda3/envs/dac_dev/bin/python host/bridge/bridge.py \
  --port /dev/ttyACM0 --rx-id 0 --raw-dir logs --status-period 1.0

# 终端 2 (bridge rx1)
/home/ruo/anaconda3/envs/dac_dev/bin/python host/bridge/bridge.py \
  --port /dev/ttyACM1 --rx-id 1 --raw-dir logs --status-period 1.0

# 终端 3 (bridge rx2)
/home/ruo/anaconda3/envs/dac_dev/bin/python host/bridge/bridge.py \
  --port /dev/ttyACM2 --rx-id 2 --raw-dir logs --status-period 1.0

# 终端 4 (camera) —— 3 个 bridge 都 frames > 280 后再启
/home/ruo/anaconda3/envs/dac_dev/bin/python host/capture/cam_capture.py \
  --camera 0 --backend any \
  --out data --session s01-r1 --duration 580 \
  --plan "1:empty_in:60,2:pos1_set1:40,3:pos2_set1:40,4:pos3_set1:40,5:pos1_set2:40,6:pos2_set2:40,7:pos3_set2:40,8:pos1_set3:40,9:pos2_set3:40,10:pos3_set3:40,11:sit:40,12:lie_supine:60,13:empty_out:60" \
  --start-on-key --overlay \
  --status-period 1.0

# 终端 5 (recorder) —— cam 启动后再启
/home/ruo/anaconda3/envs/dac_dev/bin/python host/recorder/recorder.py \
  --out data --session s01-r1 --duration 580 \
  --plan "1:empty_in:60,2:pos1_set1:40,3:pos2_set1:40,4:pos3_set1:40,5:pos1_set2:40,6:pos2_set2:40,7:pos3_set2:40,8:pos1_set3:40,9:pos2_set3:40,10:pos3_set3:40,11:sit:40,12:lie_supine:60,13:empty_out:60" \
  --start-on-key \
  --status-period 1.0
```

**关键差异点（vs dev_doc/2 §3.3）**：

| 项 | dev_doc/2 教程（Windows） | 本设计（你的 Linux + 直烧版） |
|---|---|---|
| bridge `--auto-start` | 用 | **去掉**（直烧版 RX 不解析 START 命令） |
| cam `--backend` | msmf（默认） | **any**（Linux 没有 msmf） |
| cam `--plan` | 无 | 新增（overlay + 状态机） |
| cam `--start-on-key` | 无 | 新增（远程启动 gate） |
| cam `--overlay` | 无 | 新增（右上角 OpenCV 文字） |
| recorder `--start-on-key` | 无 | 新增（与 cam 同步起跑） |
| recorder `--plan` | 无 | 新增（按 plan 时间轴打 stderr 日志） |

**启动 gate**：
- 3 个 bridge 都报 `frames > 280` 持续 5s → 开 cam
- cam 报 `fps_live > 25` → 开 recorder
- recorder 报 `frames_in > 0` → 全部到位，按 Enter 两下（cam + recorder 各一次）

---

## 4. 13 段录制协议（10 分钟，D1 plan）

**plan 与动作对应表**（D1：3 站 × 4 朝 × 3 组 + supine + 2 empty）：

| 段 | plan label | 时长 | 动作（用户在房间里做什么）|
|---|---|---|---|
| 1 | `empty_in` | 60s | 启动后立刻走出画面（房间空）|
| 2 | `pos1_set1` | 40s | 站位 1：面 N→E→S→W 各 10s |
| 3 | `pos2_set1` | 40s | 走 ~5s → 站位 2：面 N/E/S/W 各 10s |
| 4 | `pos3_set1` | 40s | 走 ~5s → 站位 3：面 N/E/S/W 各 10s |
| 5 | `pos1_set2` | 40s | 走 ~5s → 回到站位 1：面 N/E/S/W 各 10s |
| 6 | `pos2_set2` | 40s | 站位 2：面 N/E/S/W 各 10s |
| 7 | `pos3_set2` | 40s | 站位 3：面 N/E/S/W 各 10s |
| 8 | `pos1_set3` | 40s | 回到站位 1：面 N/E/S/W 各 10s |
| 9 | `pos2_set3` | 40s | 站位 2：面 N/E/S/W 各 10s |
| 10 | `pos3_set3` | 40s | 站位 3：面 N/E/S/W 各 10s |
| 11 | `sit` | 40s | 走到中间垫子坐下 |
| 12 | `lie_supine` | 60s | 在垫子上**仰卧**（**头朝 N**，按 [README L176](README.md)） |
| 13 | `empty_out` | 60s | 站起走出画面（房间空）|

> 💡 **PAM 回归任务简化**：用户 2026-07-11 明确指出，**所有动作都会被采集并加入训练**，模型对每个动作都应有泛化能力 → 不需要专门"等准备好"过渡窗口，**只需及时切换动作**。状态机省去 TRANSITION 状态。

**状态机**：

```
STANDING_BY ──[Enter]──> RECORDING(seg 1) ──[duration 到期]──> RECORDING(seg 2) ──...──> END
```

**cam_capture overlay 内容**（右上角，黄底黑字，半透明背景框）：

```
Segment 3/13 — pos2_set1
● RECORDING  12.3s / 40s
```

**overlay 字段说明**：
- `Segment 3/13` —— 当前段号 / 总段数（plan 索引）
- `pos2_set1` —— plan label（动作名，如 pos1_set1 / pos2_set2 / sit / lie_supine / empty_in 等）
- `RECORDING` —— 状态枚举值（当前只有 STANDING_BY / RECORDING / END）
- `12.3s / 40s` —— 已录制时长 / 当前段总时长

**段间 checkpoint**（每段切换前 5 秒看 1 次）：

| 进程 | 看哪个字段 | 通过条件 |
|---|---|---|
| bridge | `frames` | ≥ 280 |
| recorder | stderr 的 `frames` | ≈ 9 × 已录制秒数 |
| cam | stderr 的 `fps_live` | ≥ 25 |

**任何 checkpoint ❌ 时**：
- bridge `frames < 280` → 看 rawlog 末尾是否还在涨；不涨就 `cat /dev/ttyACM0` 看 RX 是否 reboot
- recorder `frames` 不涨 → `pgrep -f mosquitto` 看 broker；不在就重启 broker，然后 [host/tools/rawlog_to_hdf5.py](host/tools/rawlog_to_hdf5.py) 重建
- cam `fps_live < 25` → 拔插 USB 摄像头

**录制中禁令**（README §Capture rules）：
- ❌ Ctrl-C（废掉当前段；用 `--duration 580` 自然到期）
- ❌ 开 Chrome / IDE 索引 / 杀软扫描
- ❌ 拔 USB 摄像头
- ❌ 动 6 块板子
- ✅ 可看 bridge 状态行 frames 数字

> ⚠️ **你的 setup 特有风险**：
> - **USB OTG 口枚举漂移**：10 分钟内 Linux 内核可能重置 USB 设备。bridge 自动重连（bridge.py:122 `time.sleep(1)`），frames 会有 1–2s 断档，recorder 用 seq gap 容忍。
> - **loss 累积**：dev_doc/5 已记录 T4 综合 loss 4.13–4.53%，10min × 100Hz × 9 链路 ≈ 54 万帧 × 4% ≈ 22k 丢帧。csi_pipe align.py 支持 seq gap 插值，**鲁棒**。
> - **MQTT broker 死**：mosquitto pid 1072/26190 已确认在跑。如果录制时 broker 死，bridge 的 publish 会阻塞导致帧丢失——**rawlog 是真理之源**。

---

## 5. Post-recording 验证（5 分钟）

**录制结束信号**：5 个进程都退出（cam/recorder `--duration 580` 到期；bridge 按 Ctrl-C）。

**5 步验证**：

```bash
# 1) 文件存在 + 大小
ls -lh data/s01-r1-*.{h5,mp4} logs/rx*-*.rawlog

# 2) h5 结构（按 store.py 实际 schema：/meta + /video + /links/{rx}{tx}/...）
/home/ruo/anaconda3/envs/dac_dev/bin/python -c "
import h5py, glob
for h5 in glob.glob('data/s01-r1-*.h5'):
    with h5py.File(h5,'r') as f:
        print(h5)
        print('  groups:', list(f.keys()))
        print('  video/frame_idx:', f['/video/frame_idx'].shape if '/video/frame_idx' in f else 'missing')
        # 取首个 link 的 t_ns 行数作为 CSI 总帧估计
        if '/links' in f:
            link_keys = list(f['/links'].keys())
            print('  links:', link_keys)
            if link_keys:
                lk = f'/links/{link_keys[0]}/t_ns'
                print(f'  {lk}:', f[lk].shape if lk in f else 'missing')
"

# 3) mp4 帧数
ffprobe -v error -count_frames -select_streams v:0 \
  -show_entries stream=nb_read_frames data/s01-r1-*.mp4

# 4) rawlog 帧数（按 dev_doc/5 §3.3 chunked streaming 解析）
/home/ruo/anaconda3/envs/dac_dev/bin/python << 'EOF'
from pathlib import Path
import struct, glob
for rawlog in sorted(glob.glob('logs/rx*-*.rawlog')):
    n = 0
    with open(rawlog, 'rb') as f:
        head = f.read(8)
        assert head == b'CSIRAW01', head
        while True:
            rec = f.read(10)
            if len(rec) < 10: break
            t_ns, ln = struct.unpack('<QH', rec)
            payload = f.read(ln)
            if len(payload) < ln: break
            n += payload.count(b'\x1d\xc5')  # 0xC51D magic
    print(f'{rawlog}: {n} CSI frames')
EOF

# 5) h5/video/frame_idx 与 mp4 帧数一致性（按 store.py:43 实际 schema）
/home/ruo/anaconda3/envs/dac_dev/bin/python -c "
import h5py, subprocess, json, glob
h5 = glob.glob('data/s01-r1-*.h5')[0]
mp4 = glob.glob('data/s01-r1-*.mp4')[0]
with h5py.File(h5,'r') as f:
    # h5 视频帧数（按 store.py:43 字段名）
    n_h5_vid = f['/video/frame_idx'].shape[0]
    # h5 CSI 总帧估计（取 link00 的 t_ns 行数 = 单链路帧数）
    n_h5_csi = f['/links/00/t_ns'].shape[0] if '/links/00/t_ns' in f else 0
out = subprocess.check_output(['ffprobe','-v','error','-count_frames','-select_streams','v:0','-show_entries','stream=nb_read_frames','-of','json',mp4])
mp4_n = json.loads(out)['streams'][0]['nb_read_frames']
ratio = n_h5_vid / mp4_n
print(f'h5_video_frames={n_h5_vid}, mp4={mp4_n}, ratio={ratio:.3f}  (pass if 0.95 <= ratio <= 1.05)')
print(f'h5_link00_frames={n_h5_csi}  (rawlink 全帧数估计)')
"
```

**通过条件**：
- ✅ h5 `/links/00/t_ns` 第一维（即首个链路帧数）≥ 36,000
- ✅ mp4 frame count ∈ [15,500, 17,500]
- ✅ 3 份 rawlog CSI 帧数极差 < 5%
- ✅ h5 `/video/frame_idx` 行数 / mp4 帧数 ∈ [0.95, 1.05]

**任一 ❌ 补救路径**：

| ❌ 项 | 补救 |
|---|---|
| h5 缺 `/video/frame_idx` | recorder 没收到 cam/meta MQTT → 看 broker 日志；用 rawlog_to_hdf5.py 重建 |
| h5 `/links/00/t_ns` < 36k | recorder 帧率低 → 看 stderr 找 broker 重连点 |
| mp4 帧 < 15.5k | cam 录制被中断 → 看 cam stderr |
| rawlog 帧少 | bridge 死或 USB 断开 → rawlog_to_hdf5.py 重建 |
| 3 份 rawlog 极差 > 5% | 某 RX USB 不稳 → 复测 T1 单链路（dev_doc/5 §2.1）定位是哪块板 |

---

## 6. 代码改动范围

**全部 ≤ 200 行**（其中 Python ≤ 50 行，Bash ~70 行），**只改 host/ 下两个 Python 文件 + 新增 1 个 Bash 脚本**，不动 bridge/firmware/teacher/train/rt。

### 6.1 `host/capture/cam_capture.py`（~35 行增量）

| 改动 | 行数 | 位置 |
|---|---|---|
| 加 `--start-on-key` argparse flag | 2 | main() args 段 |
| 加 `--plan` argparse flag + 解析 `"1:label:sec,2:label:sec,..."` → `[(idx, label, sec)]` | 6 | main() args 段 |
| 加 `--overlay` argparse flag（默认 on）| 1 | main() args 段 |
| 加 `--no-overlay` 关闭（向后兼容）| 1 | argparse 互斥 |
| 加 `PlanState` dataclass：`cur_seg / cur_label / seg_start / state` | 8 | 新增 |
| 加 `def draw_overlay(frame, plan, state)` —— cv2.putText 右上角 + 半透明背景 | 10 | 新增 |
| 加 `if args.start_on_key: input("[gate] Press Enter to start recording...")` | 2 | 主循环前 |
| 主循环内：`now - seg_start >= plan[i].sec` → `i += 1; state="RECORDING"; print seg transition` | 3 | 主循环 |
| 每帧 `frame = draw_overlay(frame, plan, state)` | 1 | 主循环 |

**overlay 渲染细节**：
- 位置：右上角（`x = w - margin - text_w`）
- 字号：FONT_HERSHEY_SIMPLEX 0.6
- 背景框：`cv2.rectangle(frame, (x-5, y-20), (x+w+5, y+5), (0,255,255), -1)` → 黄底
- 文字色：BGR (0,0,0) 黑字
- 3 行文本按 y 偏移堆叠

### 6.2 `host/recorder/recorder.py`（~15 行增量）

| 改动 | 行数 | 位置 |
|---|---|---|
| 加 `--start-on-key` flag | 2 | main() args 段 |
| 加 `--plan` flag（同 cam 解析）| 6 | main() args 段 |
| 加 `if args.start_on_key: input("[gate] Press Enter to start recording...")` | 2 | loop_start 前 |
| 主循环加段切换检测 + stderr log | 3 | 主循环 |
| writer.set_meta("plan", args.plan) 写入 HDF5 meta | 1 | finally |

**recorder 的 `--plan` 不影响 HDF5 数据结构**（CSI/cam 都按 t_ns 写），只用于：
- stderr 段切换日志（debug 用）
- HDF5 meta 写入（后续 teacher/train 读 plan 验对齐）

### 6.3 `host/boot_recording.sh`（新增 ~70 行 Bash）

**入口接口**：
```bash
./host/boot_recording.sh [SESSION_NAME]   # 默认 s01-r1
```

**核心伪代码**（实现在 writing-plans 后写）：

```bash
#!/usr/bin/env bash
set -euo pipefail
SESSION="${1:-s01-r1}"
PYTHON=/home/ruo/anaconda3/envs/dac_dev/bin/python
TS=$(date +%Y%m%d-%H%M%S)
LOGDIR="logs/boot-${SESSION}-${TS}"
mkdir -p "$LOGDIR" data

# ① 环境预检（任一失败立即退出）
command -v mosquitto >/dev/null || { echo "❌ mosquitto not installed"; exit 1; }
pgrep mosquitto >/dev/null || mosquitto -d -p 1883
for p in 0 1 2; do [ -e "/dev/ttyACM$p" ] || { echo "❌ /dev/ttyACM$p missing"; exit 1; }; done
[ -e /dev/video0 ] || { echo "❌ /dev/video0 missing"; exit 1; }

PLAN="1:empty_in:60,2:pos1_set1:40,3:pos2_set1:40,4:pos3_set1:40,5:pos1_set2:40,6:pos2_set2:40,7:pos3_set2:40,8:pos1_set3:40,9:pos2_set3:40,10:pos3_set3:40,11:sit:40,12:lie_supine:60,13:empty_out:60"

# ② 后台启 3 个 bridge
BRIDGE_PIDS=()
for rx in 0 1 2; do
    $PYTHON host/bridge/bridge.py --port /dev/ttyACM$rx --rx-id $rx \
        --raw-dir logs --status-period 1.0 \
        > "$LOGDIR/rx$rx.log" 2>&1 &
    BRIDGE_PIDS+=($!)
done

# ③ 轮询等所有 bridge frames > 280
trap 'kill ${BRIDGE_PIDS[@]} 2>/dev/null || true; exit 1' INT TERM
echo "Waiting for 3 bridges (frames>280)..."
while true; do
    ready=0
    for rx in 0 1 2; do
        f=$(grep -oP '"frames":\s*\K\d+' "$LOGDIR/rx$rx.log" 2>/dev/null | tail -1)
        [ "${f:-0}" -gt 280 ] && ready=$((ready+1))
    done
    [ $ready -eq 3 ] && break
    sleep 2
done
echo "✓ 3 bridges ready"

# ④ 后台启 cam + recorder（用户按 Enter 才开始）
$PYTHON host/capture/cam_capture.py --camera 0 --backend any \
    --out data --session $SESSION --duration 580 --plan "$PLAN" \
    --start-on-key --overlay --status-period 1.0 \
    > "$LOGDIR/cam.log" 2>&1 &
CAM_PID=$!

$PYTHON host/recorder/recorder.py --out data --session $SESSION --duration 580 \
    --plan "$PLAN" --start-on-key --status-period 1.0 \
    > "$LOGDIR/recorder.log" 2>&1 &
REC_PID=$!

# ⑤ 等 cam/recorder 自然退出（--duration 580 到期）
wait $CAM_PID; CAM_RC=$?
wait $REC_PID; REC_RC=$?

# ⑥ 杀 bridges
kill ${BRIDGE_PIDS[@]} 2>/dev/null || true
wait ${BRIDGE_PIDS[@]} 2>/dev/null || true

# ⑦ 打印产物清单
echo "=== Recording complete ==="
echo "Cam exit: $CAM_RC | Recorder exit: $REC_RC"
echo "Boot log dir: $LOGDIR/"
ls -lh "data/${SESSION}"-*.{h5,mp4} "logs/${SESSION}"-*.rawlog 2>/dev/null \
    || { echo "❌ Expected files missing"; exit 1; }
```

**关键设计**：
- **预检短路**：mosquitto / 3 个串口 / 摄像头任一缺失立即退出，不留半成品进程
- **trap 信号**：用户 Ctrl-C 时杀光所有子进程，不留僵尸 bridge
- **轮询就绪**：用 `grep -oP '"frames":\s*\K\d+'` 解析 bridge stderr JSON，3 块都 > 280 才放行
- **每进程独立日志**：主终端只打进度，详细 stderr 在 `$LOGDIR/` 可回看
- **退出码透传**：cam/recorder 退出码原样回显，方便 CI/手动判断成功失败
- **产物清单最后一步**：用 `ls -lh` 验证文件生成（h5 + mp4 + 3 份 rawlog），缺失即报错

### 6.4 不改的文件

- ❌ `host/bridge/bridge.py` —— 端口字符串通用，串口测试已通过（dev_doc/4 + 5）
- ❌ `host/csi_pipe/*` —— clockfit / align / store 完整
- ❌ `host/recorder/mqtt_recorder.py` —— RecorderCore 完整
- ❌ `firmware/*` —— 直烧版已工作
- ❌ `teacher/*`、`train/*`、`rt/*` —— 本 dev_doc 不进 §4/§5

---

## 7. 决策依据（每条都答"为什么"）

| 决策 | 为什么 |
|---|---|
| 不用 `--auto-start` | 直烧版 RX 固件无 console 命令解析（dev_doc/4 §3.4），`START\n` 当噪声丢 |
| `--backend any` 而非 `--backend v4l2` | `any` 走 OpenCV 默认后端（Linux 下是 V4L2），不绑死版本 |
| `--start-on-key` 仅 1 次（启动），段间不阻塞 | PAM 回归对所有动作都该识别（用户 2026-07-11），不需要准备窗口 |
| `--plan` 写 HDF5 meta 而不是新字段 | csi_pipe/samples.py 已按 t_ns 对齐，plan 只是元数据，不进 sample tensor |
| overlay 默认 on | 远程操控时**眼睛看画面**比**回头看 stderr** 更自然；用 `--no-overlay` 关闭 |
| recorder 同步 `--plan` 仅 stderr 日志 | 两进程按各自 clock 推进，下凸包时钟拟合容 < 1s/10min 漂移（dev_doc/1 §3） |
| 不加 TRANSITION 状态 | 简化状态机，减少 cam_capture.py 改动；用户明示不需要 |
| 不改 bridge.py | dev_doc/4 §3.4 已确认无 bug；改 bridge 是 scope creep |
| 不动 README | 本设计是首次录制的操作 SOP，README 应等首次成功后再补"Linux + USB OTG"段落 |
| **用 boot 脚本不用 5 终端** | 一行命令启动远程可控；自动预检 + 自动清理 + 自动产物清单；任一子进程死自动 killall（避免半成品） |
| **boot 脚本放 `host/boot_recording.sh` 而非 `bin/`** | 跟 bridge/cam/recorder 同级，与"录制"动作强相关；不新建 `bin/` 目录（scope 控制） |
| **时间戳命名复用 cam_capture/recorder 自带** | 两文件已经 `time.strftime('%Y%m%d-%H%M%S')`，脚本不重复实现；避免时间戳格式分裂 |
| **每子进程独立日志到 `logs/boot-{session}-{ts}/`** | 多次录制不撞日志目录；主终端只打进度，详细 stderr 可回看调试 |
| **trap SIGINT/SIGTERM 杀光子进程** | 用户 Ctrl-C 不留半成品 bridge 进程（避免下次录制 USB 端口被占） |

---

## 8. 待用户复核 / 风险登记

### 8.1 复核清单（用户跑前要确认）

- [ ] `configs/boards.yaml` 已填 6 块板的 `com=/dev/ttyACM*` + mac
- [ ] 3 块 RX 都插好 + 充电宝 TX 都上电
- [ ] `/dev/video0` 摄像头已对准 RX 阵列
- [ ] 房间 grid 标记 + 中间瑜伽垫已布置
- [ ] 5 个终端窗口已开，dac_dev 环境已 activate
- [ ] 同意 §6 代码改动范围 ≤ 100 行、只改 host/capture + host/recorder

### 8.2 风险登记（不再"打补丁"，按 CLAUDE.md §11 红线）

| 风险 | 等级 | 缓解 |
|---|---|---|
| USB OTG 端口中途重置 | 🟡 中 | bridge 自动重连；rawlog 完整保留 |
| TX 充电宝电量不足 | 🟢 低 | 用户 2026-07-11 确认 10min 足够 |
| bridge.crc_drops 持续涨 | 🟢 低 | 已在 T4 demo 验证 CRC=0 |
| bridge startup_lost 伪影 | 🟢 低 | dev_doc/5 §3.2 已用 Δlost/Σrx 修正 |
| MQTT broker 死 | 🟡 中 | rawlog 真理之源 + rawlog_to_hdf5.py 重建 |
| cam overlay 拖累帧率 | 🟢 低 | putText 单帧 < 1ms，720p 实测无影响 |
| recorder.h5 第一维 < 45k | 🟡 中 | bridge 帧丢失 / recorder 重连；rawlog 兜底 |

---

## 9. 与其他 dev_doc 的关联

- [1-serial-sync-architecture-2026-07-08.md](1-serial-sync-architecture-2026-07-08.md) — §3 时钟拟合（recorder 不依赖此层）
- [2-reproduce-tutorial-2026-07-07.md](2-reproduce-tutorial-2026-07-07.md) — §3.1–3.6 Windows 教程基线，本设计是它的 Linux + USB OTG + TX 充电宝改写
- [3-direct-download-template-2026-07-07.md](3-direct-download-template-2026-07-07.md) — 直烧版固件来源（不重提）
- [4-rx-firmware-bringup-debug-2026-07-10.md](4-rx-firmware-bringup-debug-2026-07-10.md) — §3.4 USB OTG 双切原因
- [5-loss-throughput-baseline-2026-07-10.md](5-loss-throughput-baseline-2026-07-10.md) — §3.3 chunked streaming 解析法 + §5 综合 loss 4.13–4.53%
- [0-references-2026-07-10.xml](0-references-2026-07-10.xml) — 本会话新增 ref r023–r025（cam_capture.py / recorder.py / 新 plan argparse）

---

**最后更新**：2026-07-11 by Claude
**依据**：用户 2026-07-11 "TX 充电宝供电 + RX 在 /dev/ttyACM0-2 + /dev/video0 + dac_dev 环境" 起 4 个反馈回合的产物

---

# 🚦 下一步（brainstorming skill 流程）

按 skill 第 6 步：本文档已写到 csi-pose/dev_doc/。**请你审一遍**，确认：

1. §1 验收条件 OK？
2. §2 pre-flight 没有遗漏？
3. §3 5 进程启动命令 + 改动差异点接受？
4. §4 13 段 plan + 状态机 + overlay 内容接受？
5. §5 验证 5 步 OK？
6. §6 代码改动范围（cam_capture.py ~35 行 + recorder.py ~15 行）接受？
7. §7 决策依据每条都答了"为什么"？

**审完 → 我进 writing-plans 技能**写详细实施计划 → 你再审 → 才动键盘。