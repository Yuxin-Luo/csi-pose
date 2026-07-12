# 17 — Transition State 设计（段间 10s 缓冲）

**日期**：2026-07-12
**状态**：⏳ 待用户复核（round 3）
**目的**：解决 cam_capture 状态切换时（尤其 empty 前后）"抽象的进/退场火柴人"污染训练样本的问题

---

## Round 2 修订记录（2026-07-12）

| 用户反馈 | 修订内容 |
|---|---|
| point 1: transition 不做 CLI 参数 | 砍掉 `--transition-s` / `--transition-color` 旗标，改 `plan.py` 模块常量 `TRANSITION_S_DEFAULT = 10` |
| point 2: overlay 改鲜艳色 | `draw_overlay_transition` 用亮品红 `BGR(255,0,255)` 底 + 白字 `BGR(255,255,255)` |
| point 3: 补字段 vs trainer fallback | 选定 **trainer fallback**（5 LOC）；旧 h5 视为全 action（语义本就如此，零风险）|
| point 4: 自己再 review | 自审发现 6 个额外漏洞 A-F（duration auto / boot awk mirror / --transition-color 取消 / test-mode-first / §9 §11 清理 / §1 决策表），全部已修 |
| point 5: 先 test 再 norm | §10.0 加 test-mode-first 强制门禁（4 项验证全绿后才进 norm）|

---

## Round 3 修订记录（2026-07-12）

| 用户反馈 | 修订内容 |
|---|---|
| test/norm 互相独立；test 用 2 动作（站+蹲各 30s）| `boot_recording.sh` test mode PLAN 改为 `"1:stand:30,2:squat:30"`（effective = 70s = 2 action + 1 transition）|
|  | dev_doc/10 的 4 段 plan 不再是 test mode 默认；旧 plan 用 `--plan` 显式传 |
|  | §10.0 验证表更新为 70s / 3 段 / 单 transition 区间 |

---

## 0. 触发需求

用户在 2026-07-12 收 cam preview 时反馈：

> 状态转换的时候（尤其是 empty 的前后）进场和退场的火柴人都非常抽象，我担心这部分数据会污染最后的训练，能否考虑每个动作切换都预留 10s 的切换时间（auto），这个时间可以调整

具体担忧：
- 进入动作段时：RTMDet 还没 catch 到新姿势，stale bbox 让 RTMPose 输出扭曲骨架
- 退出动作段时：身体已部分出画，骨架 50%+ 关键点 score < 阈值，但中间帧仍有错位
- 这些"过渡帧"若带 `segment_id=动作名` 写入 h5 → trainer 会把它们当标准样本学 → 污染 PAM 标签

**目标**：在每对相邻 action 段之间插入固定 10s 缓冲，缓冲期内 CSI + cam 都继续录制，但 h5 中标记 `state=transition`，trainer 可一键过滤；cam preview 在缓冲期隐藏骨骼。

---

## 1. 设计决策（核心汇总）

| # | 决策 | 选定 | 依据 |
|---|---|---|---|
| 1 | 10s 时长计入方式 | **额外加在每段之间** | 总时长变长但不挤占原段；用户 2026-07-12 明确选此 |
| 2 | 边界范围 | **11 个 boundary**（empty_in 开头 / empty_out 结尾不加） | 见 §6 |
| 3 | 总时长 | **580 + 12×10 = 700s**（norm plan）| 比原 580s 多 120s |
| 4 | transition 期间是否录制 | **CSI + cam 都录** | 用户 2026-07-12 选；保留现场物理信息用于回看 |
| 5 | h5 segment 存储 | **`/meta/segments` JSON 范围表 + `/video/segment_idx` + `/video/state`** | 用户 2026-07-12 选；存储省、查询够快 |
| 6 | cam preview 骨骼 | **transition 期间隐藏骨骼 + overlay 改写** | 用户 2026-07-12 选；避免抽象骨架误导 |
| 7 | transition 是否可关 | **硬编码常量 `TRANSITION_S_DEFAULT = 10`，常开** | 用户 2026-07-12 point 1：直接修改代码即可，无需 CLI 旗标 |
| 8 | transition overlay 颜色 | **亮品红 `BGR(255, 0, 255)` 底 + 白色 `BGR(255, 255, 255)` 字** | 用户 2026-07-12 point 2：鲜艳、跟黄色 action 状态一眼区分 |
| 9 | 旧 h5 backward compat | **trainer 端 fallback**（缺字段视为 action）| 用户 2026-07-12 point 3；fall-demo-01 当时无 transition 概念，全段本就是 action |

---

## 2. 架构：effective_plan 单一来源

### 2.1 核心思想

把 plan 字符串"展开"成 effective_plan —— 在每对相邻 action 段之间插入 `"transition"` 段。这样 cam_capture 和 recorder **消费同一份数据结构**，避免两份并行 elapsed-arithmetic。

### 2.2 数据结构

```python
# host/capture/plan.py 扩展

# 模块级常量：段间 transition 时长（秒）。常开。要改时长直接 edit 这里。
TRANSITION_S_DEFAULT = 10

@dataclass
class PlanSegment:
    idx: int                    # 原始 plan 段编号（1-based, 用户视角）
    name: str                   # "empty_in" / "transition" / "pos1_set1" / ...
    duration_s: float
    state: str                  # "action" | "transition"

def expand_plan(plan: list) -> list[PlanSegment]:
    """把 [(idx, name, dur), ...] 展开成 effective_plan（含 transition 段）。
    transition_s 用模块常量 TRANSITION_S_DEFAULT。
    
    Example:
        plan = [(1,"empty_in",60), (2,"pos1_set1",40), (3,"empty_out",60)]
        expand_plan(plan) -> [
            PlanSegment(1,"empty_in",60,"action"),
            PlanSegment(1,"transition",10,"transition"),  # ← inserted
            PlanSegment(2,"pos1_set1",40,"action"),
            PlanSegment(2,"transition",10,"transition"),  # ← inserted
            PlanSegment(3,"empty_out",60,"action"),
        ]
    """
    if TRANSITION_S_DEFAULT <= 0:
        return [PlanSegment(i, n, d, "action") for i, n, d in plan]
    out = []
    for i, (idx, name, dur) in enumerate(plan):
        if i > 0:
            out.append(PlanSegment(plan[i-1][0], "transition", TRANSITION_S_DEFAULT, "transition"))
        out.append(PlanSegment(idx, name, dur, "action"))
    return out
```

**取消 transition 的唯一办法**：把 `TRANSITION_S_DEFAULT` 改成 `0`。expand_plan 检测到 ≤0 直接走原始 plan 路径，与现状 100% 兼容。

### 2.3 PlanState 改写

```python
@dataclass
class PlanState:
    segments: list[PlanSegment]   # ← 改：原 plan → segments（含 transition）
    cur_seg: int = 0
    seg_start: Optional[float] = None
    
    def tick(self, now: float) -> bool:
        """返回 True 表示刚跨过段边界（cam_capture 用此触发 overlay 刷新）"""
        if self.seg_start is None:
            self.seg_start = now
            return False
        if self.cur_seg >= len(self.segments) - 1:
            return False
        _, _, dur, _ = self.segments[self.cur_seg]
        if now - self.seg_start >= dur:
            self.cur_seg += 1
            self.seg_start = now
            return True
        return False
    
    @property
    def cur_segment(self) -> PlanSegment:
        return self.segments[self.cur_seg]
    
    @property
    def cur_label(self) -> str:
        return self.cur_segment.name
    
    @property
    def cur_duration(self) -> float:
        return self.cur_segment.duration_s
    
    @property
    def cur_state(self) -> str:
        return self.cur_segment.state
```

**为什么不用 dict / enum**：`PlanSegment` 是简单 dataclass，IDE 提示友好、json 序列化天然兼容。

---

## 3. h5 数据模型

### 3.1 现有结构（参考）

[host/csi_pipe/store.py:25-44](host/csi_pipe/store.py#L25-L44) 中 SessionWriter 创建：
- `/meta` (attrs)
- `/links/{rx}{tx}/{t_ns, esp_us, iq, seq, rssi, noise, boot_id}`（9 个 link）
- `/video/t_ns` (uint64, 与 cam 帧数等长)
- `/video/frame_idx` (uint32)

### 3.2 新增结构

```python
# /meta/segments (JSON 范围表，在 close() 时写)
[
  {"start_t_ns": ..., "end_t_ns": ..., "name": "empty_in",    "state": "action"},
  {"start_t_ns": ..., "end_t_ns": ..., "name": "transition",  "state": "transition"},
  {"start_t_ns": ..., "end_t_ns": ..., "name": "pos1_set1",   "state": "action"},
  ...
]

# /video/segment_idx (uint32, 与 video/t_ns 等长)
# /video/state        (uint8,  0=transition, 1=action, 与 video/t_ns 等长)
```

### 3.3 为什么不标 links

- trainer 拿到的样本 = `(csi_t_ns, label_per_csi_ts)`，label 是按 cam t_ns 索引的
- cam 帧已经带 segment → trainer 二次反查即可
- 9 个 link 重复存 580s × 130Hz × 1 byte ≈ 700KB，省下来

### 3.4 SessionWriter API 扩展

```python
# store.py
class SessionWriter:
    def __init__(...):
        ...
        self._h.create_dataset("video/segment_idx", shape=(0,), maxshape=(None,),
                               dtype=np.uint32, chunks=(1024,))
        self._h.create_dataset("video/state", shape=(0,), maxshape=(None,),
                               dtype=np.uint8, chunks=(1024,))
        self._segments_meta = []   # (start_t_ns, end_t_ns, name, state)

    def append_video(self, t_ns, frame_idx, seg_idx, state):
        with self._lock:
            self._vid.append((int(t_ns), int(frame_idx), int(seg_idx), int(state)))

    def update_segment(self, start_t_ns, end_t_ns, name, state):
        """在段切换时被 recorder 调用，记录范围表。"""
        with self._lock:
            self._segments_meta.append({
                "start_t_ns": int(start_t_ns),
                "end_t_ns": int(end_t_ns),
                "name": str(name),
                "state": str(state),
            })

    def close(self):
        with self._lock:
            ...
            self.set_meta("segments", json.dumps(self._segments_meta, ensure_ascii=False))
            self._h.close()
```

### 3.5 backward compat

- 旧 h5（无 `video/segment_idx` / `meta.segments`）读取：trainer 检测字段缺失则 `state=action` 全段有效，行为与现状一致
- 新 h5 字段缺失回退：把 `TRANSITION_S_DEFAULT` 设为 `0` 时，`expand_plan()` 输出与原 plan 同结构（不插 transition 段） → 旧 trainer 行为不变

---

## 4. recorder.py 改动

### 4.1 命令行

```python
# recorder.py
ap.add_argument("--plan", default=None, help='Plan string (stderr log + HDF5 meta)')
# --transition-s 旗标取消。transition 时长由 plan.TRANSITION_S_DEFAULT 模块常量决定。
```

### 4.2 effective_plan 计算

```python
# recorder.py main()
plan_list = parse_plan(args.plan) if args.plan else []
effective_plan = expand_plan(plan_list) if plan_list else []
```

### 4.3 主循环替换

```python
# 旧（recorder.py:107-117）
if plan_list:
    elapsed = now - t0
    cum, new_seg_idx = 0, len(plan_list) - 1
    for i, (_, _, d) in enumerate(plan_list):
        cum += d
        if elapsed < cum:
            new_seg_idx = i
            break
    if not hasattr(main, "_last_seg") or main._last_seg != new_seg_idx:
        main._last_seg = new_seg_idx
        print(f"[rec] segment {new_seg_idx + 1}/{len(plan_list)} -> {plan_list[new_seg_idx][1]}")

# 新（注意：first/last segment 的范围记录 + 用 PREV segment 而非 CUR）
if effective_plan:
    elapsed = now - t0
    new_seg_idx = -1
    cum = 0.0
    for i, seg in enumerate(effective_plan):
        cum += seg.duration_s
        if elapsed < cum:
            new_seg_idx = i
            break
    else:
        new_seg_idx = len(effective_plan) - 1
    if not hasattr(main, "_last_seg") or main._last_seg != new_seg_idx:
        # 段切换: 先关闭 PREV segment 的范围
        if hasattr(main, "_last_seg") and hasattr(main, "_seg_start_t_ns"):
            prev = effective_plan[main._last_seg]
            writer.update_segment(
                start_t_ns=main._seg_start_t_ns,
                end_t_ns=t0_ns + int((cum - seg.duration_s) * 1e9),
                name=prev.name,       # ← prev, not cur
                state=prev.state,
            )
        # 开启 NEW segment 的范围
        cur = effective_plan[new_seg_idx]
        main._seg_start_t_ns = t0_ns + int((cum - seg.duration_s) * 1e9)
        main._last_seg = new_seg_idx
        print(f"[rec] segment {new_seg_idx + 1}/{len(effective_plan)} -> "
              f"{cur.name} ({cur.state})", flush=True)

# 退出时（finally 块）关闭最后一段
# finally:
if effective_plan and hasattr(main, "_last_seg") and hasattr(main, "_seg_start_t_ns"):
    last = effective_plan[main._last_seg]
    writer.update_segment(
        start_t_ns=main._seg_start_t_ns,
        end_t_ns=time.time_ns(),
        name=last.name,
        state=last.state,
    )
```

⚠️ 边界细节见 §6 — `cum - seg.duration_s` 是当前段起点 ns，需 t0_ns 偏移。

### 4.4 RecorderCore._on_cam 改动

```python
# mqtt_recorder.py
def _on_cam(self, payload):
    ...
    t = d.get("t_ns", d.get(b"t_ns"))
    fi = d.get("frame_idx", d.get(b"frame_idx"))
    ...
    seg_idx, state = self._lookup_segment(t)   # 新增
    self.writer.append_video(int(t), int(fi), seg_idx, state)
    self.cam_frames += 1

def _lookup_segment(self, t_ns: int) -> tuple[int, int]:
    """根据 wall-clock t_ns 反查 video 帧属于哪段 + state。"""
    if not self._effective_plan or self._t0_wall_ns is None:
        return 0, 1   # 没有 plan 或未开始录制: 默认 action (backward compat)
    elapsed_s = (t_ns - self._t0_wall_ns) / 1e9
    if elapsed_s < 0:
        return 0, 1   # gate 之前到达的 cam 帧（理论上不该有）
    cum = 0.0
    for i, seg in enumerate(self._effective_plan):
        cum += seg.duration_s
        if elapsed_s < cum:
            return i, (0 if seg.state == "transition" else 1)
    return len(self._effective_plan) - 1, 1
```

### 4.5 RecorderCore 注入 effective_plan + t0_wall_ns

```python
# mqtt_recorder.py
class RecorderCore:
    def __init__(self, writer, *, on_event=None, effective_plan=None):
        ...
        self._effective_plan = effective_plan or []
        self._t0_wall_ns = None       # set after start-on-key gate opens

    def set_recording_start(self, t_wall_ns: int):
        """在 start-on-key gate 打开后被 recorder.py 调用，
        把 wall-clock t0 注入，使 _lookup_segment 能反查 cam 帧属于哪段。"""
        self._t0_wall_ns = int(t_wall_ns)
```

`recorder.py main()` 在 wire 之前 + gate 之后注入：
```python
core = RecorderCore(writer, on_event=..., effective_plan=effective_plan)
... gate ...
print(f"[rec] Recording: {path}", flush=True)
t0 = time.monotonic()
core.set_recording_start(time.time_ns())   # ← wall-clock 与 cam_capture 共享
last = t0
```

**关键**：cam_capture 在 cam/meta payload 里 `t_ns = time.time_ns()`；recorder 也用 `time.time_ns()` 设 t0 → 两边 wall-clock 对齐 → `_lookup_segment(t_ns - t0_wall_ns)` 给出 elapsed seconds。如果未来切到 CLOCK_MONOTONIC，两边都要同步改。

---

## 5. cam_capture.py 改动

### 5.1 命令行

```python
# cam_capture.py
# --transition-s 旗标取消。transition 时长由 plan.TRANSITION_S_DEFAULT 模块常量决定。
# --transition-color 旗标取消。颜色固定为亮品红 BGR(255, 0, 255)，要改改 plan.py 常量。
```

### 5.2 effective_plan 构造

```python
# cam_capture.py main()
from plan import parse_plan, PlanState, expand_plan

plan_list = parse_plan(args.plan) if args.plan else []
effective_plan = expand_plan(plan_list) if plan_list else []
plan_state = PlanState(segments=effective_plan) if effective_plan else None
```

### 5.3 主循环：plan_state.tick + skeleton gating

```python
# 主循环（替换 line 290-360 范围）
while running:
    ret, frame = cap.read()
    if not ret: break
    
    core.handle_frame(t)
    writer.write(frame)            # mp4 = 100% raw (CLAUDE.md 强约束)
    
    # fps 累计 + HUD (preview only)
    _fps_count += 1
    if now - _fps_t0 >= LIVE_FPS_WINDOW_S:
        live_fps = _fps_count / (now - _fps_t0)
        _fps_count = 0
        _fps_t0 = now
    preview = frame.copy()
    cv2.putText(preview, f"FPS {live_fps:4.1f}", FPS_HUD_POS, ...)
    
    # plan tick（段切换）
    seg_just_changed = False
    if plan_state is not None:
        seg_just_changed = plan_state.tick(now)
    
    # skeleton + overlay gating
    is_action = plan_state is None or plan_state.cur_state == "action"
    
    # overlay (preview only, gate by args.overlay)
    if plan_state is not None and args.overlay:
        if is_action:
            draw_overlay(preview, plan_state, elapsed)
        else:
            draw_overlay_transition(preview, plan_state, elapsed)
    
    # skeleton (preview only, gate by args.skeleton, AND only on action)
    if args.skeleton and skel_runner is not None and is_action:
        if skel_frame_idx % SEARCH_DET_EVERY == 0:
            dets = skel_runner.detect(frame)
            skel_bboxes = [...]
        for bbox in skel_bboxes:
            kpts = skel_runner.pose(frame, bbox)
            if kpts[:, 2].mean() >= KPT_THR:
                persons.append(kpts)
        skel_frame_idx += 1
        if persons:
            preview = skel_draw(preview, k[:, :, :2], k[:, :, 2], ...)
        cv2.putText(preview, hud, (8, preview.shape[0] - 8), ...)
    
    cv2.imshow("cam", preview)
    cv2.waitKey(1)
```

**关键不变量**：
- `writer.write(frame)` 永远在 preview 拷贝之前 → mp4 100% raw 持续保持
- skeleton gating 只多了一个 `is_action` 判断，零开销
- transition 期间 detect+pose 也不跑（节省 ~25ms/帧 → 实际 fps 还会更高）

### 5.4 draw_overlay_transition（plan.py 新增）

```python
# 颜色常量：与 draw_overlay 的黄色 BGR(0,255,255) 形成最强对比
TRANSITION_BOX_COLOR = (255, 0, 255)    # 亮品红（纯蓝+红，无绿）
TRANSITION_TEXT_COLOR = (255, 255, 255) # 白字

def draw_overlay_transition(frame, state: PlanState, elapsed_sec: float):
    """Transition 期 overlay: 亮品红 box + 白字, 显示当前 transition 段号 + 倒计时。"""
    import cv2
    h, w = frame.shape[:2]
    # 数前几个 transition 段找当前位置
    trans_n = sum(1 for s in state.segments[:state.cur_seg+1] if s.state == "transition")
    total_trans = sum(1 for s in state.segments if s.state == "transition")
    
    line1 = f"Transition {trans_n}/{total_trans} — TRANSITION"
    line2 = f"● RECORDING  {elapsed_sec - state.seg_start:.1f}s / {state.cur_duration}s"
    font, scale, thick, pad = cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1, 8
    sizes = [cv2.getTextSize(t, font, scale, thick)[0] for t in (line1, line2)]
    box_w = max(s[0] for s in sizes) + 2 * pad
    box_h = sum(s[1] for s in sizes) + 3 * pad
    x0, y0 = w - box_w - 10, 10
    cv2.rectangle(frame, (x0, y0), (x0 + box_w, y0 + box_h), TRANSITION_BOX_COLOR, -1)
    y = y0 + pad + sizes[0][1]
    for txt, (tw, th) in zip((line1, line2), sizes):
        cv2.putText(frame, txt, (x0 + pad, y), font, scale, TRANSITION_TEXT_COLOR, thick, cv2.LINE_AA)
        y += th + pad
    return frame
```

---

## 6. 边界处理（用户 2026-07-12 确认方案 a）

| 位置 | 行为 | 时长 |
|---|---|---|
| `empty_in` 开头（session 开始）| **不加前置 buffer** | 60s |
| `empty_in → pos1_set1` | 10s buffer | 60+10+40 |
| `pos1_set1 → pos2_set1` | 10s buffer | 40+10+40 |
| `pos1_set1 → pos1_set2`（同姿势不同 set）| 10s buffer | 40+10+40 |
| `lie_supine → empty_out` | 10s buffer | 60+10+60 |
| `empty_out` 结尾 | **不加后置 buffer** | 60s |

**总段数** = 13 action + 12 transition = **25 段**
**总时长** = 580 + 120 = **700s**

**为什么方案 a**：empty_in/empty_out 本身已是"无人"段，前置/后置 buffer 浪费 wall-clock 不解决任何污染。

---

## 7. boot_recording.sh 改动

```bash
# host/boot_recording.sh:115-119 替换
# 移除 --transition-s（已硬编码到 plan.py 常量）。
# --duration 由 cam_capture/recorder 自算（见 §8）。本脚本里 DURATION 仅作 ETA 日志用。
"$PYTHON" host/capture/cam_capture.py \
    --camera 0 --backend any --out "$OUT_DIR" --session "$SESSION" \
    --width 640 --height 360 --fps 30 \
    --plan "$PLAN" --start-on-key --overlay --status-period 1.0 \
    > "$LOGDIR/cam.log" 2>&1 &
CAM_PID=$!

"$PYTHON" host/recorder/recorder.py \
    --out "$OUT_DIR" --session "$SESSION" \
    --plan "$PLAN" --start-on-key --status-period 1.0 \
    > "$LOGDIR/recorder.log" 2>&1 &
REC_PID=$!
```

DURATION 本地变量仍然保留，仅用于 ETA / `=== Recording complete ===` 输出。实际停止时间由 cam_capture/recorder 自算的 effective_plan 总和决定（norm 690s / test 70s）。**用户改 plan.py 里 TRANSITION_S_DEFAULT 后，cam/recorder 自动跟随；本地 DURATION 变量如果还在 boot 里 hard-coded 580/60，只影响日志显示，不影响实际停止时间**。

为了避免本地 DURATION 漂移（用户改常量后忘记改 boot），boot_recording.sh 改用以下方式计算 ETA：

```bash
PLAN_SEGS=$(echo "$PLAN" | tr ',' '\n' | wc -l)
PLAN_BASE=$(echo "$PLAN" | awk -F: '{s+=$3} END {print s}')
DURATION=$(( PLAN_BASE + (PLAN_SEGS - 1) * 10 ))   # 10 mirror of TRANSITION_S_DEFAULT
```

如果用户改 `TRANSITION_S_DEFAULT = 15`，需要同步改 boot_recording.sh 第 3 行的 `* 10` → `* 15`。**两个地方都要改**（这是用户 point 1 的取舍：硬编码换简洁，代价是常量分散在 2 处）。

`--duration` 与 effective_plan 时长保持一致（见 §8 选项 A：默认 = `sum(s.duration_s for s in effective_plan)` = 690s）。

---

## 8. --duration 与 effective_plan 总时长的关系

**当前行为**（无 transition）：`--duration 580` 严格限制 wall-clock 到 580s，最后一段可能被截断。

**新行为**（带 transition，常量硬编码 10s）：
```python
ap.add_argument("--duration", type=float, default=None)
...
if args.duration is None or args.duration == 0:
    args.duration = sum(s.duration_s for s in effective_plan)
```

**默认 auto**：不传 `--duration` 时，cam_capture / recorder 用 `sum(effective_plan)` 当 wall-clock 上限（norm 690s / test 70s）。

**override**：传 `--duration N` 时按 N 走；若 `N < sum(effective_plan)`，最后一段会被截断，h5 范围表里那段 end_t_ns 仍按 `time.time_ns()` 写入（不会被 0 污染）。

`boot_recording.sh` **不传** `--duration`（见 §7），让 cam/recorder 自算。

---

## 9. backward compat 矩阵

**前向兼容**：旧 trainer 读新 h5
- h5py 默认访问的是 `links/{rx}{tx}/...` 和 `video/{t_ns, frame_idx}`，新字段 `video/segment_idx` / `video/state` / `meta/segments` 不被旧代码访问 → 旧 trainer 行为不变

**反向兼容**：新 trainer 读旧 h5
- 新 trainer 走 fallback：检测 `meta/segments` 不存在 → 视为全段 `state=action`，不 filter → 与改前 100% 等价
- 旧 h5（fall-demo-01 等）天然是"无 transition 概念的全 action 数据"，fallback 等价于"正确处理"，**无需 migration 脚本**

**fallback 代码模板**（trainer 端，~5 LOC）：
```python
if "segments" in h5["meta"].attrs:
    segments = json.loads(h5["meta"].attrs["segments"])
    state_per_frame = h5["video/state"][...]   # 0=transition, 1=action
else:
    state_per_frame = np.ones(len(h5["video/t_ns"][...]), dtype=np.uint8)  # 全部 action

# filter
mask = state_per_frame == 1   # 仅用 action 段训练
```

**fall-demo-01 等老 session 不需要 migration**（用户 point 3 已确认）。

| 场景 | 旧 h5（fall-demo-01 等）| 新 h5（带 transition）|
|---|---|---|
| trainer 旧代码 | 不识别新字段，正常工作 | 字段被忽略，正常工作 |
| trainer 新代码 fallback | 视为全 action | 按 /meta/segments + /video/state 过滤 |
| 数据语义 | 本就全 action（无 transition 概念）| action + transition |

---

## 10. 测试策略

### 10.0 测试顺序（用户 point 5 + round 3：test 专用 plan）

**强制门禁**：test mode 端到端跑通 → 4 项验证通过 → 才进 norm mode。

**test mode 改为 transition 专用方案**（round 3 修订）：
- 旧 test plan（dev_doc/10）：`1:empty_in:15,2:walk:25,3:lie_supine:10,4:empty_out:10` = 4 action / 60s
- 新 test plan（dev_doc/17）：`1:stand:30,2:squat:30` = 2 action + 1 transition / **70s**

新 plan 专注于 transition 验证：
- `stand` 30s：稳定站立姿势（baseline）
- transition 10s：**验证对象**（人从站到蹲的过渡期）
- `squat` 30s：稳定蹲姿（验证 transition 干净切入新姿势）

**test 与 norm 是互相独立的**（用户 round 3）：test 不必 mirror norm 的 12 段复杂度，只服务 transition 验证这一个目标。

| Step | 验证 | 通过条件 |
|---|---|---|
| 0 | 单元测试 | `pytest host/tests/test_plan_effective.py` 全绿 |
| 1 | test mode 端到端 | `./host/boot_recording.sh test s01-r1` 跑完不报错 |
| 2 | mp4 时长检查 | `ffprobe data/test/s01-r1-*.mp4 \| grep Duration` ≈ **70s**（不是 60s、不是 90s）|
| 3 | h5 segment 范围表 | `python -c "import h5py,json; h=h5py.File(...); print(json.loads(h['meta'].attrs['segments']))"` 含 **3 段**（stand / transition / squat），state 字段 = action / transition / action |
| 4 | cam preview UX | 在 30→40s transition 区间，**右上角亮品红 box + 白字**、**骨骼消失**；0-30s 和 40-70s 时段黄色 box + 骨骼正常 |

**test mode 4 项全绿后**，才跑 norm（用户 point 5 的门禁）。

**follow-up**：dev_doc/10（test mode design）的 4 段 plan rationale 现在与新的 test mode 不一致。如需保留旧 plan 用于其他验证（loss/FPS），改用 `--plan` 参数显式传入，或在 boot_recording.sh 加 env override。dev_doc/10 的更新不在本 PR 范围。

### 10.1 单元

```python
# host/tests/test_plan_effective.py
def test_expand_plan_no_transition():
    """把常量临时改成 0 测：expand_plan 返回原 plan"""
    import plan
    saved = plan.TRANSITION_S_DEFAULT
    plan.TRANSITION_S_DEFAULT = 0
    try:
        assert expand_plan([(1,"a",30),(2,"b",20)]) == [
            PlanSegment(1,"a",30,"action"),
            PlanSegment(2,"b",20,"action"),
        ]
    finally:
        plan.TRANSITION_S_DEFAULT = saved

def test_expand_plan_with_transition():
    assert expand_plan([(1,"a",30),(2,"b",20)]) == [
        PlanSegment(1,"a",30,"action"),
        PlanSegment(1,"transition",10,"transition"),
        PlanSegment(2,"b",20,"action"),
    ]

def test_expand_norm_full():
    """norm plan 13 action 段 → effective 25 段（13+12），总 700s

    13 segments from boot_recording.sh norm PLAN:
    1:empty_in:60, 2-10:pos{1,2,3}_set{1,2,3}:40×9, 11:sit:40,
    12:lie_supine:60, 13:empty_out:60
    Actions: 60 + 9×40 + 40 + 60 + 60 = 580s
    Transitions: 12 × 10 = 120s
    Total: 700s
    """
    norm = [(1,"empty_in",60),(2,"pos1_set1",40),(3,"pos2_set1",40),(4,"pos3_set1",40),
            (5,"pos1_set2",40),(6,"pos2_set2",40),(7,"pos3_set2",40),(8,"pos1_set3",40),
            (9,"pos2_set3",40),(10,"pos3_set3",40),(11,"sit",40),(12,"lie_supine",60),
            (13,"empty_out",60)]
    eff = expand_plan(norm)
    assert len(eff) == 25   # 13 action + 12 transition
    assert sum(s.duration_s for s in eff) == 700.0
    assert sum(1 for s in eff if s.state == "transition") == 12

def test_expand_test_mode():
    """test plan 2 段（round 3），effective 应有 3 段（2 action + 1 transition），总 70s"""
    test = [(1,"stand",30),(2,"squat",30)]
    eff = expand_plan(test)
    assert len(eff) == 3   # stand, transition, squat
    assert sum(s.duration_s for s in eff) == 70.0
    assert sum(1 for s in eff if s.state == "transition") == 1
    # 首尾验证: 第一段是 stand action, 最后一段是 squat action
    assert eff[0].name == "stand" and eff[0].state == "action"
    assert eff[-1].name == "squat" and eff[-1].state == "action"
```

### 10.2 集成

```bash
# === Step 1: test mode 端到端 ===
./host/boot_recording.sh test s01-r1
# 确认 (test plan = 2 action 段 60s → 60 + 1×10 = 70s total)

# === Step 2: mp4 时长 ===
ffprobe data/test/s01-r1-*.mp4 | grep Duration      # 应 ~70s (不是 60s, 不是 90s)

# === Step 3: h5 segment 范围表 ===
python -c "import h5py,json; h=h5py.File('data/test/s01-r1-*.h5'); print(json.loads(h['meta'].attrs['segments']))"
# 应打印 3 段 (stand / transition / squat)

# === Step 4: h5 video 帧标记 ===
python -c "import h5py; h=h5py.File('data/test/s01-r1-*.h5'); print(h['video/state'][...])"
# 应是 0/1 交替的 array, 总长 = 视频帧数

# === Step 5 (test 全绿后): norm mode 端到端 ===
./host/boot_recording.sh norm s01-r1
# ffprobe 时长 ~700s, h5 有 25 段（13 action + 12 transition）
```

### 10.3 手动（cam preview 视觉）

1. 跑 test mode（70s）
2. 看 cam preview 在 30→40s 这段 transition 区间：
   - 右上角 **亮品红 box + 白字** "Transition 1/1 — TRANSITION" + "● RECORDING  7.0s / 10s"
   - **骨骼消失**（画面只有 fps HUD 左上角）
3. 看 h5 的 `/meta/segments` 包含 3 段（stand/transition/squat），state 字段 = action/transition/action

### 10.4 回归测试

- 把 `TRANSITION_S_DEFAULT = 0` 跑 test mode → mp4 应 ~60s（与现状一致），h5 应无 transition 段 → 确认 disable path 不破坏现状
- 把 `TRANSITION_S_DEFAULT` 改回 10 → 全部按新行为走

---

## 11. 文件改动清单

| 文件 | 改动 |
|---|---|
| `host/capture/plan.py` | + `TRANSITION_S_DEFAULT` 常量, + `PlanSegment` dataclass, + `expand_plan(plan)`, + `draw_overlay_transition()`, + `TRANSITION_BOX_COLOR / TRANSITION_TEXT_COLOR` 常量, 改 `PlanState` 用 segments 列表 |
| `host/csi_pipe/store.py` | + `video/segment_idx`, + `video/state` datasets, + `update_segment()`, `append_video()` 加 seg_idx + state 参数 |
| `host/csi_pipe/mqtt_recorder.py` | `RecorderCore.__init__` + `effective_plan` 参数, + `set_recording_start()`, + `_lookup_segment()`, `_on_cam` 加 segment 标记 |
| `host/recorder/recorder.py` | 主循环替换为 effective_plan arithmetic, gate 后调 `set_recording_start()`, finally 关闭最后一段 |
| `host/capture/cam_capture.py` | PlanState 用 effective_plan, skeleton gating 加 `is_action`, overlay 分支 (`draw_overlay` vs `draw_overlay_transition`) |
| `host/boot_recording.sh` | 移除 `--transition-s` 旗标, 移除 `--duration` 旗标, DURATION 改为本地 awk 计算, 注释里 mirror `TRANSITION_S_DEFAULT=10` |
| `host/tests/test_plan_effective.py` | + 4 个 expand_plan 单元测试（含常量 monkeypatch 测 disable path）|

**总行数**：~140 行新增 + ~30 行改动（比初版减少，因为砍了 2 个 CLI 旗标）

**改动 ripple 风险**：
- plan.py 是 cam_capture.py 和 recorder.py 都会 import 的共享模块 → 一处改三处测
- store.py 是 recorder 唯一写路径 → 单测覆盖 `append_video` 新签名 + `update_segment` 锁行为
- boot_recording.sh 改动只影响 `cam_capture` 和 `recorder` 调用行 → 现有 4 个 sanity check 路径不动

---

## 12. 待用户复核事项

| # | 问题 | 默认 | 状态 |
|---|---|---|---|
| 1 | ~~§8 选项 A（duration 自动 = plan 总和）~~ → 已选定 A | ✅ | resolved |
| 2 | ~~transition 是否做成 CLI flag~~ → 硬编码常量 | ✅ | resolved |
| 3 | ~~draw_overlay_transition 颜色~~ → 亮品红 BGR(255,0,255) | ✅ | resolved |
| 4 | ~~fall-demo-01 等旧 h5 backward compat~~ → trainer fallback | ✅ | resolved |
| 5 | 测试顺序：先 test mode 70s 再 norm 690s | ✅ | resolved（§10.0 gate）|
| 6 | 整体方案（effective_plan 单一来源 + h5 segment 范围表 + skeleton 仅 action 段 + trainer fallback + test-mode-first）是否通过？ | 待签字 | ⏳ |

---

**维护者**：Claude
**依据**：用户 2026-07-12 三轮澄清选择 + CLAUDE.md §8 文档规范 + csi-pose CLAUDE.md §9.3 决策可追溯