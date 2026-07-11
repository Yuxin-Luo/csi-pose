# s01-r1 首次录制 MVP 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline, MVP-style per Agent Rules.txt §10).

**Goal:** 跑通 csi-pose 首次 `s01-r1` 录制（10min, D1 plan）拿到 h5/mp4/rawlog 三件套。

**Architecture:** 在 csi-pose `host/{capture,recorder}/` 加最小增量（plan 解析 + overlay + start-on-key 三个 CLI 概念）+ 新增 1 个 Bash 编排脚本 `host/boot_recording.sh`。不动 firmware / csi_pipe / teacher / train / rt。

**Tech Stack:** Python 3.10+ / conda `dac_dev` / h5py / cv2 / paho-mqtt / pyserial / mosquitto（已跑）/ ESP32-S3 直烧版固件（dev_doc/4 + 5 验证）。

---

## Global Constraints

- **Conda 环境**：`/home/ruo/anaconda3/envs/dac_dev/bin/python`
- **录制时长**：`--duration 580`（D1 plan 总时长）
- **Plan 字符串**：`"1:empty_in:60,2:pos1_set1:40,3:pos2_set1:40,4:pos3_set1:40,5:pos1_set2:40,6:pos2_set2:40,7:pos3_set2:40,8:pos1_set3:40,9:pos2_set3:40,10:pos3_set3:40,11:sit:40,12:lie_supine:60,13:empty_out:60"`
- **数据路径**：`data/s01-r1-YYYYMMDD-HHMMSS.{h5,mp4}` + `logs/rx{N}-YYYYMMDD-HHMMSS.rawlog`
- **不动**：`host/bridge/bridge.py`、`host/csi_pipe/*`、`firmware/*`、`teacher/*`、`train/*`、`rt/*`、`README*`
- **测试原则（Agent Rules.txt §10）**：MVP 验证为主，每 Task ≤ 5 行 smoke test，**不写 pytest、不写测试文件**——只验证"能跑、参数对、阻塞行为正确"
- **CLAUDE.md §9.4 基线复现**：dev_doc/5 §5 已验证 §2.5 链路通过；本计划不重新 baseline

---

## 文件结构

| 文件 | 动作 | 行数 | 责任 |
|---|---|---|---|
| `host/capture/plan.py` | 新建 | ~50 | `parse_plan` + `PlanState` + `draw_overlay` 3 个纯函数 |
| `host/capture/cam_capture.py` | 改 | +30 | 加 `--start-on-key` `--plan` `--overlay` 三个 flag + 主循环集成 |
| `host/recorder/recorder.py` | 改 | +15 | 加 `--start-on-key` `--plan` + HDF5 meta 写入 |
| `host/boot_recording.sh` | 新建 | ~70 | 5 进程编排 + 预检 + 产物清单 |

**总计**：~165 行（Python ~95 + Bash ~70）。原 cam_capture/recorder 核心逻辑**不动**。

---

## Task 1: Python 三个文件改完（cam + recorder + plan.py）

**Files:**
- Create: `host/capture/plan.py`
- Modify: `host/capture/cam_capture.py`
- Modify: `host/recorder/recorder.py`

---

- [ ] **Step 1: 创建 `host/capture/plan.py`**

```python
"""Plan parser + segment state + overlay renderer for cam_capture.

Pure functions: no MQTT / no serial / no cv2 at import-time.
cv2 imported lazily inside draw_overlay so unit-imports stay light.
"""
from dataclasses import dataclass


def parse_plan(s: str):
    """Parse "1:label:60,2:label:40,..." -> [(1,"label",60), ...]."""
    out = []
    for seg in s.split(","):
        parts = seg.strip().split(":")
        if len(parts) != 3:
            raise ValueError(f"malformed plan segment: {seg!r}")
        idx, label, dur = int(parts[0]), parts[1].strip(), int(parts[2])
        out.append((idx, label, dur))
    return out


@dataclass
class PlanState:
    plan: list
    cur_seg: int = 0
    cur_label: str = ""
    seg_start: float | None = None

    def __post_init__(self):
        self.cur_label = self.plan[0][1]

    def tick(self, now: float) -> bool:
        if self.seg_start is None or self.cur_seg >= len(self.plan) - 1:
            return False
        _, _, dur = self.plan[self.cur_seg]
        if now - self.seg_start >= dur:
            self.cur_seg += 1
            self.cur_label = self.plan[self.cur_seg][1]
            self.seg_start = now
            return True
        return False

    @property
    def total_segments(self) -> int:
        return len(self.plan)

    @property
    def cur_duration(self) -> int:
        return self.plan[self.cur_seg][2]


def draw_overlay(frame, state: PlanState, elapsed_sec: float):
    """Draw segment overlay in upper-right corner (in-place)."""
    import cv2
    h, w = frame.shape[:2]
    line1 = f"Segment {state.cur_seg + 1}/{state.total_segments} — {state.cur_label}"
    line2 = f"● RECORDING  {elapsed_sec:.1f}s / {state.cur_duration}s"
    font, scale, thick, pad = cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1, 8
    sizes = [cv2.getTextSize(t, font, scale, thick)[0] for t in (line1, line2)]
    box_w = max(s[0] for s in sizes) + 2 * pad
    box_h = sum(s[1] for s in sizes) + 3 * pad
    x0, y0 = w - box_w - 10, 10
    cv2.rectangle(frame, (x0, y0), (x0 + box_w, y0 + box_h), (0, 255, 255), -1)
    y = y0 + pad + sizes[0][1]
    for txt, (tw, th) in zip((line1, line2), sizes):
        cv2.putText(frame, txt, (x0 + pad, y), font, scale, (0, 0, 0), thick, cv2.LINE_AA)
        y += th + pad
    return frame
```

- [ ] **Step 2: 改 `host/capture/cam_capture.py`**

在文件**顶部**的 `import` 块（`import argparse` 附近）加一行：

```python
sys.path.insert(0, str(Path(__file__).resolve().parent))
from plan import parse_plan, PlanState, draw_overlay  # noqa: E402
```

在 `main()` 的 argparse 段（`--status-period` 行后）加 4 行：

```python
ap.add_argument("--start-on-key", action="store_true", help="Wait for Enter before recording")
ap.add_argument("--plan", default=None, help='Plan string "1:label:60,2:label:40,..."')
ap.add_argument("--overlay", action="store_true", default=True, help="Draw segment overlay")
ap.add_argument("--no-overlay", dest="overlay", action="store_false")
```

在 `args = ap.parse_args()` 之后**立刻**加：

```python
plan_list = parse_plan(args.plan) if args.plan else []
plan_state = PlanState(plan_list) if plan_list else None
```

在 `core = CamCore(sink)` 之后、`cap = cv2.VideoCapture(...)` 之前加：

```python
if args.start_on_key:
    input("[gate] Press Enter to start recording (cam+csi)...")
```

在主 `while True:` 循环内（`ret, frame = cap.read()` 和 `t = time.time_ns()` 之后），把：

```python
    if ret:
        core.handle_frame(t)
        writer.write(frame)
```

替换为：

```python
    if ret:
        if plan_state is not None:
            if plan_state.seg_start is None:
                plan_state.seg_start = time.monotonic()
            elapsed = time.monotonic() - plan_state.seg_start
            if plan_state.tick(time.monotonic()):
                print(f"[cam] segment {plan_state.cur_seg + 1}/{plan_state.total_segments} -> {plan_state.cur_label}", flush=True)
                plan_state.seg_start = time.monotonic()
                elapsed = 0.0
            if args.overlay:
                draw_overlay(frame, plan_state, elapsed)
        core.handle_frame(t)
        writer.write(frame)
```

- [ ] **Step 3: 改 `host/recorder/recorder.py`**

在文件**顶部** import 块加：

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "capture"))
from plan import parse_plan  # noqa: E402
```

在 argparse 段（`--status-period` 行后）加 2 行：

```python
ap.add_argument("--start-on-key", action="store_true", help="Wait for Enter before recording")
ap.add_argument("--plan", default=None, help='Plan string (stderr log + HDF5 meta only)')
```

在 `args = ap.parse_args()` 之后**立刻**加：

```python
plan_list = parse_plan(args.plan) if args.plan else []
```

在 `client.loop_start()` 之后、`print(f"[rec] Recording: ...")` 之前加：

```python
if args.start_on_key:
    input("[gate] Press Enter to start recording (recorder)...")
```

在主 `while True:` 循环内（`time.sleep(0.2)` 之后）加：

```python
            if plan_list:
                elapsed = now - t0
                cum, new_seg_idx = 0, len(plan_list) - 1
                for i, (_, _, d) in enumerate(plan_list):
                    cum += d
                    if elapsed < cum:
                        new_seg_idx = i
                        break
                if not hasattr(recorder_main, "_last_seg") or recorder_main._last_seg != new_seg_idx:
                    recorder_main._last_seg = new_seg_idx
                    print(f"[rec] segment {new_seg_idx + 1}/{len(plan_list)} -> {plan_list[new_seg_idx][1]}", flush=True)
```

> 注意：`recorder_main` 是这个 `main()` 函数本身（Python 默认就是此名）。如果你的 `main()` 叫别的名，把 `recorder_main` 改成你的函数名。

在 `finally:` 块内、`client.loop_stop()` 之前加：

```python
        if args.plan:
            writer.set_meta("plan", args.plan)
```

- [ ] **Step 4: Smoke test — 帮助输出含新 flag**

Run:
```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python host/capture/cam_capture.py --help 2>&1 | grep -E "start-on-key|plan|overlay"
/home/ruo/anaconda3/envs/dac_dev/bin/python host/recorder/recorder.py --help 2>&1 | grep -E "start-on-key|plan"
```

Expected: 6 行 flag（cam 4 个 + recorder 2 个）。如果 `< 6` 行 → 检查 import 是否成功。

- [ ] **Step 5: Smoke test — plan 字符串能解析**

Run:
```bash
/home/ruo/anaconda3/envs/dac_dev/bin/python -c "
import sys; sys.path.insert(0,'host/capture')
from plan import parse_plan
p = parse_plan('1:empty_in:60,2:pos1_set1:40,11:sit:40,13:empty_out:60')
print(f'OK segments={len(p)} last={p[-1]}')
"
```

Expected: `OK segments=4 last=(13, 'empty_out', 60)`

- [ ] **Step 6: Smoke test — `--start-on-key` 真阻塞 stdin**

Run:
```bash
timeout 3 /home/ruo/anaconda3/envs/dac_dev/bin/python host/capture/cam_capture.py \
    --camera 0 --backend any --out /tmp/cam_test --session test \
    --plan "1:empty_in:60" --start-on-key 2>&1 | head -3 || true
timeout 3 /home/ruo/anaconda3/envs/dac_dev/bin/python host/recorder/recorder.py \
    --out /tmp/rec_test --session test --plan "1:empty_in:60" --start-on-key 2>&1 | head -3 || true
```

Expected: 两行 `[gate] Press Enter to start recording ...` 各自输出，然后 3 秒 timeout 杀掉。**两个都阻塞** = gate 工作。

- [ ] **Step 7: Commit**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
git add host/capture/plan.py host/capture/cam_capture.py host/recorder/recorder.py
git commit -m "feat(host): add --start-on-key + --plan + overlay for first recording"
```

---

## Task 2: boot_recording.sh（Bash 编排）

**Files:**
- Create: `host/boot_recording.sh`

---

- [ ] **Step 1: 创建 `host/boot_recording.sh`**

```bash
#!/usr/bin/env bash
# boot_recording.sh — orchestrate 5 processes for one csi-pose recording.
# Usage: ./host/boot_recording.sh [SESSION_NAME]
set -euo pipefail

SESSION="${1:-s01-r1}"
PYTHON="/home/ruo/anaconda3/envs/dac_dev/bin/python"
TS="$(date +%Y%m%d-%H%M%S)"
LOGDIR="logs/boot-${SESSION}-${TS}"
DURATION=580

PLAN="1:empty_in:60,2:pos1_set1:40,3:pos2_set1:40,4:pos3_set1:40,5:pos1_set2:40,6:pos2_set2:40,7:pos3_set2:40,8:pos1_set3:40,9:pos2_set3:40,10:pos3_set3:40,11:sit:40,12:lie_supine:60,13:empty_out:60"

mkdir -p "$LOGDIR" data

# ① 预检
command -v mosquitto >/dev/null || { echo "❌ mosquitto not installed"; exit 1; }
pgrep mosquitto >/dev/null || mosquitto -d -p 1883
for p in 0 1 2; do [ -e "/dev/ttyACM$p" ] || { echo "❌ /dev/ttyACM$p missing"; exit 1; }; done
[ -e /dev/video0 ] || { echo "❌ /dev/video0 missing"; exit 1; }
echo "✓ preflight OK (session=$SESSION, ts=$TS)"

# ② 后台启 3 bridge
BRIDGE_PIDS=()
for rx in 0 1 2; do
    "$PYTHON" host/bridge/bridge.py --port "/dev/ttyACM$rx" --rx-id "$rx" \
        --raw-dir logs --status-period 1.0 \
        > "$LOGDIR/rx$rx.log" 2>&1 &
    BRIDGE_PIDS+=($!)
done

# ③ 轮询等所有 bridge frames > 280
trap 'kill ${BRIDGE_PIDS[@]:-} 2>/dev/null || true; exit 1' INT TERM
echo "Waiting for 3 bridges (frames > 280)..."
while true; do
    ready=0
    for rx in 0 1 2; do
        f=$(grep -oP '"frames":\s*\K\d+' "$LOGDIR/rx$rx.log" 2>/dev/null | tail -1)
        [ "${f:-0}" -gt 280 ] && ready=$((ready + 1))
    done
    [ "$ready" -eq 3 ] && break
    sleep 2
done
echo "✓ 3 bridges ready"

# ④ 后台启 cam + recorder（用户按 Enter 开始）
"$PYTHON" host/capture/cam_capture.py \
    --camera 0 --backend any --out data --session "$SESSION" --duration "$DURATION" \
    --plan "$PLAN" --start-on-key --overlay --status-period 1.0 \
    > "$LOGDIR/cam.log" 2>&1 &
CAM_PID=$!

"$PYTHON" host/recorder/recorder.py \
    --out data --session "$SESSION" --duration "$DURATION" \
    --plan "$PLAN" --start-on-key --status-period 1.0 \
    > "$LOGDIR/recorder.log" 2>&1 &
REC_PID=$!

# ⑤ 等自然退出
wait "$CAM_PID"; CAM_RC=$?
wait "$REC_PID"; REC_RC=$?

# ⑥ 杀 bridges
kill "${BRIDGE_PIDS[@]}" 2>/dev/null || true
wait "${BRIDGE_PIDS[@]}" 2>/dev/null || true

# ⑦ 打印产物
echo ""
echo "=== Recording complete ==="
echo "Cam exit: $CAM_RC | Recorder exit: $REC_RC"
shopt -s nullglob
H5=(data/${SESSION}-*.h5); MP4=(data/${SESSION}-*.mp4); RAW=(logs/rx*-${TS}.rawlog)
if [ "${#H5[@]}" -eq 0 ] || [ "${#MP4[@]}" -eq 0 ] || [ "${#RAW[@]}" -lt 3 ]; then
    echo "❌ Missing: h5=${#H5[@]} mp4=${#MP4[@]} raw=${#RAW[@]}"
    ls -lh "${H5[@]}" "${MP4[@]}" "${RAW[@]}" 2>/dev/null || true
    exit 1
fi
ls -lh "${H5[0]}" "${MP4[0]}" "${RAW[@]}"
echo "Boot log: $LOGDIR/"
```

- [ ] **Step 2: 加执行权限**

```bash
chmod +x host/boot_recording.sh
```

- [ ] **Step 3: 语法检查**

Run:
```bash
bash -n host/boot_recording.sh && echo "✓ bash syntax OK"
```

Expected: `✓ bash syntax OK`

- [ ] **Step 4: 预检快速验证（5 秒 dry-run）**

Run:
```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
timeout 5 bash -x host/boot_recording.sh smoke 2>&1 | grep -E "preflight|bridge|Bridge|mqtt" | head -10 || true
```

Expected: 看到 `✓ preflight OK` + 看到 bridge 子进程被启动。timeout 5s 杀掉时 bridge 子进程会留 **3 个** `bridge.py` 进程在后台，需要手动 `pkill -f bridge.py` 清掉。

- [ ] **Step 5: 清理残留进程**

```bash
pkill -f "host/bridge/bridge.py" || true
sleep 1
pgrep -f "host/bridge/bridge.py" || echo "✓ no leftover bridges"
```

Expected: `✓ no leftover bridges`

- [ ] **Step 6: Commit**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
git add host/boot_recording.sh
git commit -m "feat(host): add boot_recording.sh — orchestrate 5 processes"
```

---

## Task 3: 端到端录制（手动）

**Files:** 无

**Goal:** 跑一次完整 10 分钟录制，验证 §1 全部 4 项验收。

---

- [ ] **Step 1: 物理环境预检**

```bash
ls /dev/ttyACM{0,1,2} /dev/video0
pgrep mosquitto >/dev/null && echo "✓ mosquitto running" || mosquitto -d -p 1883
/home/ruo/anaconda3/envs/dac_dev/bin/python -c "import h5py, cv2, paho.mqtt.client, serial; print('✓ deps OK')"
```

Expected: 全部通过。

- [ ] **Step 2: 启动 boot 脚本**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
./host/boot_recording.sh s01-r1
```

Expected（顺序）：
1. `✓ preflight OK`
2. `Waiting for 3 bridges (frames > 280)...`（约 5–10s 后）
3. `✓ 3 bridges ready`
4. 然后 cam 和 recorder **各自**在 stdin 上阻塞（`[gate] Press Enter...` 各一次）

**注意**：bash 主进程的 stdin 被两个 Python 子进程共享，按 Enter 一次只解锁**先到的那个**。两次 Enter 解锁两个。

- [ ] **Step 3: 走到位置 1，按 2 次 Enter（解锁 cam + recorder）**

cam 解锁后开始录 mp4 + overlay；recorder 解锁后开始写 h5 + rawlog。**总时长 580s 录制 + ~50s 走路 = ~10.3 分钟墙钟**。

期间观察主终端：
- 每 5s 一行 `[cam] segment X/13 -> YYYY`
- 每 5s 一行 `[rec] segment X/13 -> YYYY`
- 每 5s 一行 bridge JSON frames 数字

按 D1 plan 表执行动作（[dev_doc/6 §4](../6-s01-r1-13min-recording-design-2026-07-11.md) 详表）：

| 段 | label | 时长 | 你做什么 |
|---|---|---|---|
| 1 | empty_in | 60s | 站到 RX 阵列外（无遮挡）|
| 2–4 | pos1/2/3_set1 | 40s × 3 | 走 ~5s → 站位 1/2/3 面 N/E/S/W |
| 5–7 | set2 | 40s × 3 | 走 ~5s → 回到站位 1 → 再 2/3 |
| 8–10 | set3 | 40s × 3 | 同上 |
| 11 | sit | 40s | 走到中间垫子坐下 |
| 12 | lie_supine | 60s | **仰卧头朝 N** |
| 13 | empty_out | 60s | 站起走出 |

- [ ] **Step 4: 录制完成**

Expected output（主脚本末尾）：
```
=== Recording complete ===
Cam exit: 0 | Recorder exit: 0

-rw-r--r-- ... data/s01-r1-202607XX-HHMMSS.h5
-rw-r--r-- ... data/s01-r1-202607XX-HHMMSS.mp4
-rw-r--r-- ... logs/rx0-202607XX-HHMMSS.rawlog
-rw-r--r-- ... logs/rx1-202607XX-HHMMSS.rawlog
-rw-r--r-- ... logs/rx2-202607XX-HHMMSS.rawlog
```

- [ ] **Step 5: §5 验收 5 步**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose

# 1) 文件存在 + 大小
ls -lh data/s01-r1-*.{h5,mp4} logs/rx*-*.rawlog

# 2) h5 结构
/home/ruo/anaconda3/envs/dac_dev/bin/python -c "
import h5py, glob
for h5 in glob.glob('data/s01-r1-*.h5'):
    with h5py.File(h5,'r') as f:
        print(h5)
        print('  groups:', list(f.keys()))
        print('  video/frame_idx:', f['/video/frame_idx'].shape)
        if '/links' in f:
            lk = list(f['/links'].keys())[0]
            print(f'  /links/{lk}/t_ns:', f[f'/links/{lk}/t_ns'].shape)
"

# 3) mp4 帧数
ffprobe -v error -count_frames -select_streams v:0 \
  -show_entries stream=nb_read_frames data/s01-r1-*.mp4

# 4) rawlog 帧数
/home/ruo/anaconda3/envs/dac_dev/bin/python << 'EOF'
import struct, glob
for rawlog in sorted(glob.glob('logs/rx*-*.rawlog')):
    n = 0
    with open(rawlog, 'rb') as f:
        assert f.read(8) == b'CSIRAW01'
        while True:
            rec = f.read(10)
            if len(rec) < 10: break
            _, ln = struct.unpack('<QH', rec)
            p = f.read(ln)
            if len(p) < ln: break
            n += p.count(b'\x1d\xc5')
    print(f'{rawlog}: {n} CSI frames')
EOF

# 5) h5/video/frame_idx vs mp4
/home/ruo/anaconda3/envs/dac_dev/bin/python -c "
import h5py, subprocess, json, glob
h5 = glob.glob('data/s01-r1-*.h5')[0]; mp4 = glob.glob('data/s01-r1-*.mp4')[0]
with h5py.File(h5,'r') as f:
    n = f['/video/frame_idx'].shape[0]
out = subprocess.check_output(['ffprobe','-v','error','-count_frames','-select_streams','v:0','-show_entries','stream=nb_read_frames','-of','json',mp4])
mp4_n = json.loads(out)['streams'][0]['nb_read_frames']
print(f'h5_video={n} mp4={mp4_n} ratio={n/mp4_n:.3f} (pass if 0.95-1.05)')
"
```

**通过条件**：
- ✅ h5 `/links/00/t_ns` 第一维 ≥ 36,000
- ✅ mp4 frame count ∈ [15,500, 17,500]
- ✅ 3 份 rawlog CSI 帧数极差 < 5%
- ✅ h5 `/video/frame_idx` / mp4 ∈ [0.95, 1.05]

- [ ] **Step 6: 写 dev_doc/8 复盘**

新建 `dev_doc/8-s01-r1-recording-report-2026-07-11.md`，记录：
- 实际文件大小 + 4 项验收实测值
- 录制过程中遇到的问题
- 与 dev_doc/6 §8.2 7 项风险的对比

```bash
git add dev_doc/8-*.md
git commit -m "docs: s01-r1 first recording session report"
```

---

## Plan summary

| Task | 文件改动 | smoke test | commit |
|---|---|---|---|
| 1 | plan.py +50 / cam +30 / recorder +15 | 4 行命令 | ✅ |
| 2 | boot_recording.sh +70 | bash -n + dry-run | ✅ |
| 3 | 无 | 端到端录制 + §5 验收 | ✅ |

**总改动 ~165 行，3 个 commit，3 个 smoke test 命令**（符合 Agent Rules.txt §10 MVP 风格）。

---

**Plan complete and saved to `dev_doc/7-s01-r1-13min-recording-implementation-2026-07-11.md`.**

按 Agent Rules.txt §10（**迅捷开发 + MVP 验证**）风格执行 — 现在直接做？回复 **"做"** 我开始 Task 1。

如果想 subagent 派单（每 Task 一审），回复 **"subagent"**。