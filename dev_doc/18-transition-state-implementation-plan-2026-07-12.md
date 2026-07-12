# Transition State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在每对相邻 action 段之间自动插入 10s transition 缓冲，h5 标记 `state=transition`，cam preview 隐藏骨骼，让 trainer 一键 filter 过渡帧。

**Architecture:** effective_plan 单一来源（plan.py 展开）→ cam_capture 和 recorder 各自消费；plan.py 用 `TRANSITION_S_DEFAULT = 10` 模块常量；h5 加 `/meta/segments` JSON 范围表 + `/video/segment_idx` + `/video/state`；test mode 改为 2 动作（stand + squat）+ 1 transition（70s）专门验证本特性。

**Tech Stack:** Python 3, h5py, pytest 8.4.2, OpenCV, paho-mqtt, existing csi-pose host stack.

**Spec:** [dev_doc/17-transition-state-design-2026-07-12.md](17-transition-state-design-2026-07-12.md)（待复核 round 3）

---

## Global Constraints

来自 CLAUDE.md / csi-pose CLAUDE.md 的硬约束（每条任务都隐含适用）：

- **MP4 100% raw**：cam_capture.py 中 `writer.write(frame)` 必须在 `preview = frame.copy()` 之前；所有 cv2.putText / draw_skeleton / draw_overlay 都画在 preview，绝不碰 frame（spec §5.3）
- **API 速率**：RPM < 200, TPM < 10M（无影响，本任务纯本地）
- **dev_doc 规范**：每次任务产出对应 dev_doc 章节；本 plan 即 dev_doc/18
- **backward compat**：旧 h5（fall-demo-01 等）不破坏；trainer 端 fallback（5 LOC），不写 migration 脚本
- **TDD**：每任务先写 failing test，验证 fail，再写 minimal impl，验证 pass，再 commit
- **过渡时长**：唯一可改点是 `plan.py:TRANSITION_S_DEFAULT` 常量；CLI 旗标已砍
- **test mode 默认 plan**：`"1:stand:30,2:squat:30"`（effective = 70s = 2 action + 1 transition）；norm mode 不变

## File Structure

| 文件 | 状态 | 职责 |
|---|---|---|
| `host/capture/plan.py` | 改 | PlanSegment dataclass + expand_plan + PlanState refactor + draw_overlay_transition + TRANSITION_S_DEFAULT 常量 |
| `host/csi_pipe/store.py` | 改 | SessionWriter 新增 `/video/segment_idx` + `/video/state` + update_segment() |
| `host/csi_pipe/mqtt_recorder.py` | 改 | RecorderCore 增加 effective_plan + set_recording_start + _lookup_segment + 修改 _on_cam |
| `host/recorder/recorder.py` | 改 | argparse + effective_plan 算术 + finally 关最后段 |
| `host/capture/cam_capture.py` | 改 | PlanState 用 effective_plan + skeleton gating `is_action` + overlay 分支 |
| `host/boot_recording.sh` | 改（已 round 3 完成）| test mode PLAN 改 2-action |
| `host/tests/__init__.py` | 新 | 空 marker |
| `host/tests/test_plan_effective.py` | 新 | 4 个 expand_plan 单测 |
| `host/tests/test_plan_state.py` | 新 | 3 个 PlanState 单测 |
| `host/tests/test_overlay_transition.py` | 新 | 1 个 draw_overlay_transition 单测 |
| `host/tests/test_store_segment.py` | 新 | 3 个 SessionWriter 新字段单测 |
| `host/tests/test_recorder_lookup.py` | 新 | 5 个 RecorderCore 单测 |

---

## Task 1: plan.py — TRANSITION_S_DEFAULT + PlanSegment + expand_plan

**Files:**
- Create: `host/tests/__init__.py`（空文件）
- Create: `host/tests/test_plan_effective.py`
- Modify: `host/capture/plan.py:1-67`

**Interfaces:**
- Consumes: nothing（foundation task）
- Produces:
  - `plan.TRANSITION_S_DEFAULT: int = 10`
  - `plan.PlanSegment` dataclass（fields: `idx: int`, `name: str`, `duration_s: float`, `state: str`）
  - `plan.expand_plan(plan: list[tuple[int, str, int]]) -> list[PlanSegment]`

- [ ] **Step 1: 创建测试目录**

```bash
mkdir -p /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose/host/tests
touch /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose/host/tests/__init__.py
```

- [ ] **Step 2: 写 4 个 failing tests**

文件 `host/tests/test_plan_effective.py`：

```python
"""Unit tests for plan.expand_plan() — see dev_doc/17 §2.2."""
import sys
from pathlib import Path

# 让 import 能找到 host/capture/plan.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "capture"))

import plan


def test_expand_plan_with_transition_default():
    """TRANSITION_S_DEFAULT=10 时，2 段 plan 展开成 3 段（2 action + 1 transition）"""
    result = plan.expand_plan([(1, "stand", 30), (2, "squat", 30)])
    assert len(result) == 3
    assert sum(s.duration_s for s in result) == 70.0
    assert sum(1 for s in result if s.state == "transition") == 1
    assert result[0].name == "stand" and result[0].state == "action"
    assert result[1].name == "transition" and result[1].state == "transition"
    assert result[2].name == "squat" and result[2].state == "action"


def test_expand_plan_no_transition_when_constant_zero():
    """TRANSITION_S_DEFAULT=0 时，2 段 plan 展开成 2 段（无 transition）"""
    saved = plan.TRANSITION_S_DEFAULT
    plan.TRANSITION_S_DEFAULT = 0
    try:
        result = plan.expand_plan([(1, "a", 30), (2, "b", 20)])
        assert len(result) == 2
        assert sum(s.duration_s for s in result) == 50.0
        assert all(s.state == "action" for s in result)
    finally:
        plan.TRANSITION_S_DEFAULT = saved


def test_expand_norm_plan_25_segments_700s():
    """norm 13 action 段 → effective 25 段（13+12），总 700s

    Based on actual norm PLAN in host/boot_recording.sh:60:
    1:empty_in:60,2:pos1_set1:40,3:pos2_set1:40,4:pos3_set1:40,5:pos1_set2:40,
    6:pos2_set2:40,7:pos3_set2:40,8:pos1_set3:40,9:pos2_set3:40,10:pos3_set3:40,
    11:sit:40,12:lie_supine:60,13:empty_out:60
    Actions: empty_in(60) + 9*pos(40) + sit(40) + lie_supine(60) + empty_out(60) = 580s
    Transitions: 12 * 10 = 120s
    Total: 580 + 120 = 700s
    """
    norm = [
        (1, "empty_in", 60), (2, "pos1_set1", 40), (3, "pos2_set1", 40),
        (4, "pos3_set1", 40), (5, "pos1_set2", 40), (6, "pos2_set2", 40),
        (7, "pos3_set2", 40), (8, "pos1_set3", 40), (9, "pos2_set3", 40),
        (10, "pos3_set3", 40), (11, "sit", 40), (12, "lie_supine", 60),
        (13, "empty_out", 60),
    ]
    eff = plan.expand_plan(norm)
    assert len(eff) == 25
    assert sum(s.duration_s for s in eff) == 700.0
    assert sum(1 for s in eff if s.state == "transition") == 12


def test_expand_test_plan_3_segments_70s():
    """test 2 action 段 → effective 3 段（2+1），总 70s（round 3 验证）"""
    test = [(1, "stand", 30), (2, "squat", 30)]
    eff = plan.expand_plan(test)
    assert len(eff) == 3
    assert sum(s.duration_s for s in eff) == 70.0
    # 第一段是 stand action, 最后一段是 squat action
    assert eff[0].name == "stand" and eff[0].state == "action"
    assert eff[-1].name == "squat" and eff[-1].state == "action"
    # 中间只有 1 个 transition
    assert eff[1].state == "transition"
```

- [ ] **Step 3: 运行测试，验证全部 fail**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python -m pytest host/tests/test_plan_effective.py -v
```

Expected: 全部 FAIL（`module 'plan' has no attribute 'PlanSegment'` / `expand_plan`）

- [ ] **Step 4: 实现 plan.py 扩展**

修改 [host/capture/plan.py](host/capture/plan.py)，在文件顶部加：

```python
"""Plan parser + segment state + overlay renderer for cam_capture.

Pure functions: no MQTT / no serial / no cv2 at import-time.
cv2 imported lazily inside draw_overlay so unit-imports stay light.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple


# ─── Transition 常量（dev_doc/17 §2.2）──────────────────────────────────
# 段间 transition 时长（秒）。要改时长 edit 这里，全局生效。
# 设 0 → expand_plan() 走原始路径，与 transition 特性引入前 100% 等价。
TRANSITION_S_DEFAULT = 10


@dataclass
class PlanSegment:
    """effective_plan 的一段（含 transition 段）。"""
    idx: int                    # 原始 plan 段编号（1-based）
    name: str                   # "empty_in" / "transition" / "pos1_set1" / ...
    duration_s: float
    state: str                  # "action" | "transition"


def expand_plan(plan: list) -> list:
    """把 [(idx, name, dur), ...] 展开成 effective_plan，每对相邻段间插 transition。

    Args:
        plan: [(idx, name, dur_seconds), ...] 原始 plan 列表

    Returns:
        list[PlanSegment]: effective_plan。TRANSITION_S_DEFAULT=0 时等同原 plan。

    Example:
        >>> expand_plan([(1,"a",30),(2,"b",20)])
        [PlanSegment(1,"a",30,"action"),
         PlanSegment(1,"transition",10,"transition"),
         PlanSegment(2,"b",20,"action")]
    """
    if TRANSITION_S_DEFAULT <= 0:
        return [PlanSegment(int(i), str(n), float(d), "action")
                for i, n, d in plan]
    out = []
    for i, (idx, name, dur) in enumerate(plan):
        if i > 0:
            out.append(PlanSegment(int(plan[i-1][0]), "transition",
                                   float(TRANSITION_S_DEFAULT), "transition"))
        out.append(PlanSegment(int(idx), str(name), float(dur), "action"))
    return out
```

**保留原 `parse_plan` / `PlanState` / `draw_overlay` 函数不变**（Task 2 改 PlanState，Task 3 加 draw_overlay_transition）。

- [ ] **Step 5: 运行测试，验证全部 pass**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python -m pytest host/tests/test_plan_effective.py -v
```

Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
git add host/capture/plan.py host/tests/__init__.py host/tests/test_plan_effective.py
git commit -m "feat(host/plan): TRANSITION_S_DEFAULT + PlanSegment + expand_plan (dev_doc/17 §2.2)"
```

---

## Task 2: plan.py — PlanState 重构（用 segments 列表）

**Files:**
- Create: `host/tests/test_plan_state.py`
- Modify: `host/capture/plan.py`

**Interfaces:**
- Consumes: `plan.expand_plan()` (from Task 1)
- Produces:
  - `plan.PlanState.segments: list[PlanSegment]`（替换原 `plan: list`）
  - `plan.PlanState.cur_segment: PlanSegment` (property)
  - `plan.PlanState.cur_state: str` (property，返回 "action" | "transition")
  - `plan.PlanState.cur_label: str` (property，等价 `cur_segment.name`)
  - `plan.PlanState.cur_duration: float` (property)

- [ ] **Step 1: 写 3 个 failing tests**

文件 `host/tests/test_plan_state.py`：

```python
"""Unit tests for plan.PlanState with effective_plan (segments list)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "capture"))

import plan


def test_plan_state_initial_uses_first_segment():
    """new PlanState(segments) → cur_state 等于第一段 state"""
    segments = plan.expand_plan([(1, "stand", 30), (2, "squat", 30)])
    # 3 段: stand(action), transition, squat(action)
    ps = plan.PlanState(segments=segments)
    assert ps.cur_state == "action"
    assert ps.cur_label == "stand"
    assert ps.cur_duration == 30.0


def test_plan_state_tick_crosses_action_to_transition():
    """elapsed > action 段 duration → tick() 推进到 transition 段"""
    segments = plan.expand_plan([(1, "stand", 30), (2, "squat", 30)])
    ps = plan.PlanState(segments=segments)
    # 初始 seg_start = None，第一次 tick 设置 seg_start
    assert ps.seg_start is None
    changed = ps.tick(100.0)
    assert changed is False  # 第一次 tick 只设 seg_start, 不算段切换
    assert ps.seg_start == 100.0
    # elapsed=30.5 时进入 transition 段
    changed = ps.tick(130.5)
    assert changed is True
    assert ps.cur_state == "transition"
    assert ps.cur_label == "transition"
    assert ps.cur_duration == 10.0


def test_plan_state_tick_crosses_transition_to_next_action():
    """elapsed > transition 段 duration → tick() 推进到下一 action 段"""
    segments = plan.expand_plan([(1, "stand", 30), (2, "squat", 30)])
    ps = plan.PlanState(segments=segments)
    ps.tick(0.0)               # 初始化
    ps.tick(35.0)              # → transition
    assert ps.cur_state == "transition"
    changed = ps.tick(45.0)    # → squat
    assert changed is True
    assert ps.cur_state == "action"
    assert ps.cur_label == "squat"
```

- [ ] **Step 2: 运行测试，验证全部 fail**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python -m pytest host/tests/test_plan_state.py -v
```

Expected: 全部 FAIL（`PlanState.segments` 不存在 / `cur_state` 等属性缺失）

- [ ] **Step 3: 重构 PlanState**

修改 [host/capture/plan.py](host/capture/plan.py)，替换原 `PlanState` dataclass 为：

```python
@dataclass
class PlanState:
    segments: list            # list[PlanSegment]，来自 expand_plan()
    cur_seg: int = 0
    seg_start: Optional[float] = None

    def __post_init__(self):
        if not self.segments:
            raise ValueError("PlanState requires non-empty segments list")

    def tick(self, now: float) -> bool:
        """当 now 跨过当前段边界时推进 cur_seg，返回 True 表示刚切换。"""
        if self.seg_start is None:
            self.seg_start = now
            return False
        if self.cur_seg >= len(self.segments) - 1:
            return False
        if now - self.seg_start >= self.cur_segment.duration_s:
            self.cur_seg += 1
            self.seg_start = now
            return True
        return False

    @property
    def cur_segment(self) -> "PlanSegment":
        return self.segments[self.cur_seg]

    @property
    def cur_label(self) -> str:
        return self.cur_segment.name

    @property
    def cur_duration(self):
        return self.cur_segment.duration_s

    @property
    def cur_state(self) -> str:
        return self.cur_segment.state

    @property
    def total_segments(self) -> int:
        return len(self.segments)
```

**保留 `parse_plan` 函数**（recorder.py 和 cam_capture.py 都还在用它）。

- [ ] **Step 4: 运行测试，验证全部 pass**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python -m pytest host/tests/test_plan_state.py -v
```

Expected: 3 passed

- [ ] **Step 5: 检查 cam_capture.py 是否仍能 import（不破坏现状）**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python -c "from plan import PlanState; print('OK')"
```

Expected: `OK`（PlanState 仍可构造，但用旧调用方式 `PlanState(plan=[...])` 会失败 —— 这是预期的，Task 7 才会更新调用方）

如果这一步发现 cam_capture.py 旧调用断，先 commit 此 task，然后在 Task 7 修复。

- [ ] **Step 6: Commit**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
git add host/capture/plan.py host/tests/test_plan_state.py
git commit -m "refactor(host/plan): PlanState 用 segments 列表 + cur_state 属性 (dev_doc/17 §2.3)"
```

---

## Task 3: plan.py — draw_overlay_transition + TRANSITION 颜色常量

**Files:**
- Create: `host/tests/test_overlay_transition.py`
- Modify: `host/capture/plan.py`

**Interfaces:**
- Consumes: `plan.PlanState` (from Task 2), cv2 (lazy import)
- Produces:
  - `plan.TRANSITION_BOX_COLOR = (255, 0, 255)` (亮品红 BGR)
  - `plan.TRANSITION_TEXT_COLOR = (255, 255, 255)` (白)
  - `plan.draw_overlay_transition(frame, state, elapsed_sec)` → 修改 frame in-place, 返回 frame

- [ ] **Step 1: 写 1 个 failing test（视觉抽样验证）**

文件 `host/tests/test_overlay_transition.py`：

```python
"""Unit test for plan.draw_overlay_transition() — see dev_doc/17 §5.4."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "capture"))

import plan


def test_draw_overlay_transition_writes_magenta_box():
    """在 transition 段绘制亮品红 box（与黄色 action 状态区分）"""
    segments = plan.expand_plan([(1, "stand", 30), (2, "squat", 30)])
    ps = plan.PlanState(segments=segments)
    ps.tick(0.0)
    ps.tick(35.0)             # → transition
    assert ps.cur_state == "transition"

    frame = np.zeros((360, 640, 3), dtype=np.uint8)  # 黑色底
    out = plan.draw_overlay_transition(frame, ps, elapsed_sec=38.5)

    # 返回值就是 frame 本身（in-place 修改）
    assert out is frame
    # 抽样：右上角区域应该有亮品红像素（BGR(255,0,255)）
    # box 在右上 (x0, y0) 附近，size 取决于文字宽度
    h, w = frame.shape[:2]
    sample = frame[15:25, w-200:w-50, :]  # 右上角区域
    # 至少有一些像素是亮品红 (B=255, G=0, R=255)
    magenta_pixels = np.sum(
        (sample[:, :, 0] == 255) & (sample[:, :, 1] == 0) & (sample[:, :, 2] == 255)
    )
    assert magenta_pixels > 100, f"期望 ≥100 个亮品红像素，实际 {magenta_pixels}"
```

- [ ] **Step 2: 运行测试，验证 fail**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python -m pytest host/tests/test_overlay_transition.py -v
```

Expected: FAIL（`module 'plan' has no attribute 'draw_overlay_transition'`）

- [ ] **Step 3: 实现 draw_overlay_transition**

修改 [host/capture/plan.py](host/capture/plan.py)，在文件末尾追加：

```python
# ─── Transition overlay 颜色（dev_doc/17 §1 decision 8）───────────────
# 与 action 状态黄色 (0,255,255) 形成最强对比（纯蓝+红，无绿通道重叠）
TRANSITION_BOX_COLOR = (255, 0, 255)    # 亮品红
TRANSITION_TEXT_COLOR = (255, 255, 255) # 白


def draw_overlay_transition(frame, state: "PlanState", elapsed_sec: float):
    """Transition 期 overlay: 亮品红 box + 白字。

    显示内容:
      - 第 1 行: "Transition X/Y — TRANSITION"  (X/Y 是 transition 段计数)
      - 第 2 行: "● RECORDING  {elapsed_in_seg}s / {seg_duration}s"

    Args:
        frame: numpy.ndarray (in-place 修改)
        state: PlanState, 当前应在 transition 段
        elapsed_sec: 从录制开始的总秒数

    Returns:
        frame (同一对象, in-place 修改)
    """
    import cv2
    h, w = frame.shape[:2]

    # 数前几个 transition 段找当前位置
    trans_n = sum(1 for s in state.segments[:state.cur_seg + 1]
                  if s.state == "transition")
    total_trans = sum(1 for s in state.segments if s.state == "transition")

    line1 = f"Transition {trans_n}/{total_trans} — TRANSITION"
    seg_elapsed = elapsed_sec - (state.seg_start or elapsed_sec)
    line2 = f"● RECORDING  {seg_elapsed:.1f}s / {state.cur_duration}s"

    font, scale, thick, pad = cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1, 8
    sizes = [cv2.getTextSize(t, font, scale, thick)[0] for t in (line1, line2)]
    box_w = max(s[0] for s in sizes) + 2 * pad
    box_h = sum(s[1] for s in sizes) + 3 * pad
    x0, y0 = w - box_w - 10, 10
    cv2.rectangle(frame, (x0, y0), (x0 + box_w, y0 + box_h),
                  TRANSITION_BOX_COLOR, -1)
    y = y0 + pad + sizes[0][1]
    for txt, (tw, th) in zip((line1, line2), sizes):
        cv2.putText(frame, txt, (x0 + pad, y), font, scale,
                    TRANSITION_TEXT_COLOR, thick, cv2.LINE_AA)
        y += th + pad
    return frame
```

- [ ] **Step 4: 运行测试，验证 pass**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python -m pytest host/tests/test_overlay_transition.py -v
```

Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
git add host/capture/plan.py host/tests/test_overlay_transition.py
git commit -m "feat(host/plan): draw_overlay_transition 亮品红+白字 (dev_doc/17 §5.4)"
```

---

## Task 4: store.py — video segment datasets + update_segment

**Files:**
- Create: `host/tests/test_store_segment.py`
- Modify: `host/csi_pipe/store.py:25-123`

**Interfaces:**
- Consumes: existing SessionWriter API
- Produces:
  - `SessionWriter(path, meta=...)` → 新建 h5 时自动创建 `video/segment_idx` (uint32) + `video/state` (uint8) datasets
  - `SessionWriter.append_video(t_ns, frame_idx, seg_idx, state)` → 4 参数签名
  - `SessionWriter.update_segment(start_t_ns, end_t_ns, name, state)` → 追加到 `_segments_meta` 列表
  - `SessionWriter.close()` → `meta/segments` attr 含 JSON 范围表

- [ ] **Step 1: 写 3 个 failing tests**

文件 `host/tests/test_store_segment.py`：

```python
"""Unit tests for SessionWriter segment support — see dev_doc/17 §3.4."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "csi_pipe"))

import h5py
import store


def test_session_writer_creates_segment_datasets():
    """SessionWriter() 自动创建 video/segment_idx + video/state datasets"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.h5"
        writer = store.SessionWriter(path, meta={"session": "test"})
        writer.close()

        with h5py.File(path, "r") as h:
            assert "video/segment_idx" in h
            assert "video/state" in h
            assert h["video/segment_idx"].dtype == "uint32"
            assert h["video/state"].dtype == "uint8"


def test_append_video_writes_seg_idx_and_state():
    """append_video(t_ns, frame_idx, seg_idx, state) 同时写 segment 标记"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.h5"
        writer = store.SessionWriter(path, meta={"session": "test"})
        writer.append_video(1000, 0, seg_idx=0, state=1)
        writer.append_video(1033, 1, seg_idx=0, state=1)
        writer.append_video(1066, 2, seg_idx=1, state=0)  # transition
        writer.close()

        with h5py.File(path, "r") as h:
            seg_idx = h["video/segment_idx"][...]
            state = h["video/state"][...]
            assert list(seg_idx) == [0, 0, 1]
            assert list(state) == [1, 1, 0]


def test_update_segment_writes_meta_segments_on_close():
    """update_segment() 累积到 _segments_meta，close() 写入 meta/segments"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.h5"
        writer = store.SessionWriter(path, meta={"session": "test"})
        writer.update_segment(start_t_ns=0, end_t_ns=30_000_000_000,
                              name="stand", state="action")
        writer.update_segment(start_t_ns=30_000_000_000, end_t_ns=40_000_000_000,
                              name="transition", state="transition")
        writer.update_segment(start_t_ns=40_000_000_000, end_t_ns=70_000_000_000,
                              name="squat", state="action")
        writer.close()

        with h5py.File(path, "r") as h:
            segments = json.loads(h["meta"].attrs["segments"])
            assert len(segments) == 3
            assert segments[0]["name"] == "stand" and segments[0]["state"] == "action"
            assert segments[1]["name"] == "transition" and segments[1]["state"] == "transition"
            assert segments[2]["name"] == "squat" and segments[2]["state"] == "action"
            # 时间戳精确
            assert segments[0]["start_t_ns"] == 0
            assert segments[1]["start_t_ns"] == 30_000_000_000
```

- [ ] **Step 2: 运行测试，验证全部 fail**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python -m pytest host/tests/test_store_segment.py -v
```

Expected: 全部 FAIL（datasets 不存在 / append_video 签名不兼容）

- [ ] **Step 3: 扩展 SessionWriter**

修改 [host/csi_pipe/store.py](host/csi_pipe/store.py)：

**`__init__` 末尾追加（line 44 附近）**：

```python
        # dev_doc/17 §3.2: per-video-frame segment 标记
        self._h.create_dataset("video/segment_idx", shape=(0,), maxshape=(None,),
                               dtype=np.uint32, chunks=(1024,))
        self._h.create_dataset("video/state", shape=(0,), maxshape=(None,),
                               dtype=np.uint8, chunks=(1024,))
        self._segments_meta = []   # dev_doc/17 §3.4
```

**`append_video` 签名改 4 参数（替换原 line 67-71）**：

```python
    def append_video(self, t_ns, frame_idx, *, seg_idx=0, state=1):
        """dev_doc/17 §3.4: seg_idx/state 是关键字参数，默认 action (backward compat)。"""
        with self._lock:
            self._vid.append((int(t_ns), int(frame_idx), int(seg_idx), int(state)))
            if len(self._vid) >= 1024:
                self._flush_video()
```

**新增 `update_segment` 方法（紧跟 append_video 后）**：

```python
    def update_segment(self, *, start_t_ns, end_t_ns, name, state):
        """dev_doc/17 §3.4: 累积段范围表，close() 时写入 meta/segments JSON。"""
        with self._lock:
            self._segments_meta.append({
                "start_t_ns": int(start_t_ns),
                "end_t_ns": int(end_t_ns),
                "name": str(name),
                "state": str(state),
            })
```

**`_flush_video` 改写（替换原 line 94-104）**：

```python
    def _flush_video(self):
        if not self._vid:
            return
        ts = self._h["video/t_ns"]
        fi = self._h["video/frame_idx"]
        si = self._h["video/segment_idx"]
        st = self._h["video/state"]
        n = ts.shape[0]
        m = len(self._vid)
        ts.resize(n + m, axis=0)
        fi.resize(n + m, axis=0)
        si.resize(n + m, axis=0)
        st.resize(n + m, axis=0)
        ts[n:] = np.asarray([v[0] for v in self._vid], np.uint64)
        fi[n:] = np.asarray([v[1] for v in self._vid], np.uint32)
        si[n:] = np.asarray([v[2] for v in self._vid], np.uint32)
        st[n:] = np.asarray([v[3] for v in self._vid], np.uint8)
        self._vid.clear()
```

**`close` 末尾追加（line 122 后）**：

```python
        # dev_doc/17 §3.4: 写入 meta/segments 范围表
        self.set_meta("segments", json.dumps(self._segments_meta, ensure_ascii=False))
```

**注意**：line 119 已有 `self.set_meta("frames_total", ...)` 和 `self.set_meta("links", ...)`。新的 `set_meta("segments", ...)` 必须**在 `self._h.close()` 之前**调用（即 line 123 之前）。

- [ ] **Step 4: 运行测试，验证全部 pass**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python -m pytest host/tests/test_store_segment.py -v
```

Expected: 3 passed

- [ ] **Step 5: 检查现有 recorder.py 是否仍能调用（backward compat）**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
grep -n "append_video" host/csi_pipe/mqtt_recorder.py
```

Expected: 看到 `self.writer.append_video(int(t), int(fi))` —— **这调用因 seg_idx/state 是关键字参数且有默认值**，仍能工作。✅ backward compat 保留。

- [ ] **Step 6: Commit**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
git add host/csi_pipe/store.py host/tests/test_store_segment.py
git commit -m "feat(host/store): video segment 标记 + update_segment + meta/segments (dev_doc/17 §3)"
```

---

## Task 5: mqtt_recorder.py — RecorderCore segment 支持

**Files:**
- Create: `host/tests/test_recorder_lookup.py`
- Modify: `host/csi_pipe/mqtt_recorder.py:51-138`

**Interfaces:**
- Consumes: `store.SessionWriter` (from Task 4), `plan.expand_plan()` (from Task 1)
- Produces:
  - `RecorderCore(writer, *, on_event=None, effective_plan=None)` — 新增 `effective_plan` kw 参数
  - `RecorderCore.set_recording_start(t_wall_ns: int)` — 在 gate 打开后调用
  - `RecorderCore._lookup_segment(t_ns: int) -> tuple[int, int]` — 返回 `(seg_idx, state)`
  - `RecorderCore._on_cam(payload)` — 用 seg_idx/state 调用 `writer.append_video(...)`

- [ ] **Step 1: 写 5 个 failing tests**

文件 `host/tests/test_recorder_lookup.py`：

```python
"""Unit tests for RecorderCore segment lookup — see dev_doc/17 §4.4-§4.5."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "csi_pipe"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "capture"))

import store
import mqtt_recorder
import plan


def _make_core(effective_plan):
    """构造 RecorderCore + 临时 SessionWriter，返回 (core, writer, h5_path)。"""
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "test.h5"
    writer = store.SessionWriter(path, meta={"session": "test"})
    core = mqtt_recorder.RecorderCore(writer, effective_plan=effective_plan)
    return core, writer, path, tmp


def test_lookup_segment_no_plan_returns_action_default():
    """没传 effective_plan → 所有帧视为 action (backward compat)"""
    core, writer, path, tmp = _make_core(effective_plan=None)
    try:
        seg_idx, state = core._lookup_segment(12345)
        assert seg_idx == 0
        assert state == 1
    finally:
        writer.close()
        tmp.cleanup()


def test_lookup_segment_before_set_recording_start_returns_action():
    """set_recording_start 没调 → 视为 action (backward compat)"""
    eff = plan.expand_plan([(1, "stand", 30), (2, "squat", 30)])
    core, writer, path, tmp = _make_core(effective_plan=eff)
    try:
        seg_idx, state = core._lookup_segment(12345)
        assert seg_idx == 0
        assert state == 1
    finally:
        writer.close()
        tmp.cleanup()


def test_lookup_segment_in_action_section():
    """t_wall_ns 落在 action 段 → state=1"""
    eff = plan.expand_plan([(1, "stand", 30), (2, "squat", 30)])
    # 3 段: stand(30) + transition(10) + squat(30) = 70s
    core, writer, path, tmp = _make_core(effective_plan=eff)
    try:
        core.set_recording_start(1_000_000_000)  # t0 = 1s
        # t = 1 + 15 = 16s → 落在 stand (0-30s)
        seg_idx, state = core._lookup_segment(16_000_000_000)
        assert seg_idx == 0  # stand
        assert state == 1     # action
    finally:
        writer.close()
        tmp.cleanup()


def test_lookup_segment_in_transition_section():
    """t_wall_ns 落在 transition 段 → state=0"""
    eff = plan.expand_plan([(1, "stand", 30), (2, "squat", 30)])
    core, writer, path, tmp = _make_core(effective_plan=eff)
    try:
        core.set_recording_start(1_000_000_000)
        # t = 1 + 35 = 36s → 落在 transition (30-40s)
        seg_idx, state = core._lookup_segment(36_000_000_000)
        assert seg_idx == 1  # transition
        assert state == 0     # transition
    finally:
        writer.close()
        tmp.cleanup()


def test_lookup_segment_in_next_action_section():
    """t_wall_ns 落在第二 action 段 → state=1, seg_idx=2"""
    eff = plan.expand_plan([(1, "stand", 30), (2, "squat", 30)])
    core, writer, path, tmp = _make_core(effective_plan=eff)
    try:
        core.set_recording_start(1_000_000_000)
        # t = 1 + 55 = 56s → 落在 squat (40-70s)
        seg_idx, state = core._lookup_segment(56_000_000_000)
        assert seg_idx == 2  # squat
        assert state == 1     # action
    finally:
        writer.close()
        tmp.cleanup()
```

- [ ] **Step 2: 运行测试，验证全部 fail**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python -m pytest host/tests/test_recorder_lookup.py -v
```

Expected: 全部 FAIL（`effective_plan` 关键字参数不识别 / `_lookup_segment` 不存在）

- [ ] **Step 3: 扩展 RecorderCore**

修改 [host/csi_pipe/mqtt_recorder.py](host/csi_pipe/mqtt_recorder.py)：

**`RecorderCore.__init__` 加 effective_plan 参数（替换原 line 51-63）**：

```python
class RecorderCore:
    def __init__(self, writer, *, on_event=None, effective_plan=None):
        self.writer = writer
        self.on_event = on_event
        self._unwrap = {}    # rx_id -> TimeUnwrapper
        self._links = {}     # (rx,tx) -> LinkTracker
        self.frames = 0
        self.crc_drops = 0
        self.cam_frames = 0
        self.cam_errors = 0
        self.unknown = 0
        self.reboots = 0
        self.wraps = 0       # u32 wrap cumulative count (per-rx sum)
        # dev_doc/17 §4.5: segment 查找依赖
        self._effective_plan = effective_plan or []
        self._t0_wall_ns = None  # set_recording_start() 在 gate 后调用
```

**`handle` 方法签名加 t_recv_ns 参数（替换原 line 65）**：

注：原 handle 已有 `t_recv_ns=0` 默认参数，无需改。

**`_on_cam` 加 segment 标记（替换原 line 104-119）**：

```python
    def _on_cam(self, payload):
        if msgpack is None:
            self.cam_errors += 1
            return
        try:
            d = msgpack.unpackb(payload)
            if not isinstance(d, dict):
                raise ValueError("cam/meta is not a dict")
            t = d.get("t_ns", d.get(b"t_ns"))
            fi = d.get("frame_idx", d.get(b"frame_idx"))
            if t is None or fi is None:
                raise ValueError("t_ns/frame_idx missing")
            # dev_doc/17 §4.4: 反查段 + state
            seg_idx, state = self._lookup_segment(int(t))
            self.writer.append_video(int(t), int(fi),
                                     seg_idx=seg_idx, state=state)
            self.cam_frames += 1
        except Exception:
            self.cam_errors += 1
```

**新增 `set_recording_start` 和 `_lookup_segment` 方法（紧跟 __init__ 后）**：

```python
    def set_recording_start(self, t_wall_ns: int):
        """dev_doc/17 §4.5: 在 start-on-key gate 打开后由 recorder.py 调用，
        把 wall-clock t0 注入，使 _lookup_segment 能反查 cam 帧属于哪段。"""
        self._t0_wall_ns = int(t_wall_ns)

    def _lookup_segment(self, t_ns: int) -> tuple:
        """根据 wall-clock t_ns 反查 video 帧属于哪段 + state。

        Returns:
            (seg_idx, state) — seg_idx 是 effective_plan 索引, state 是 0/1 (transition/action)。
            无 plan / 未开始录制 / t_ns < t0_wall_ns: 视为 action (backward compat)。
        """
        if not self._effective_plan or self._t0_wall_ns is None:
            return 0, 1
        elapsed_s = (t_ns - self._t0_wall_ns) / 1e9
        if elapsed_s < 0:
            return 0, 1
        cum = 0.0
        for i, seg in enumerate(self._effective_plan):
            cum += seg.duration_s
            if elapsed_s < cum:
                return i, (0 if seg.state == "transition" else 1)
        return len(self._effective_plan) - 1, 1
```

- [ ] **Step 4: 运行测试，验证全部 pass**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python -m pytest host/tests/test_recorder_lookup.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
git add host/csi_pipe/mqtt_recorder.py host/tests/test_recorder_lookup.py
git commit -m "feat(host/mqtt_recorder): segment lookup + set_recording_start (dev_doc/17 §4)"
```

---

## Task 6: recorder.py — main loop 集成 + finally 关闭最后段

**Files:**
- Modify: `host/recorder/recorder.py:95-135`

**Interfaces:**
- Consumes: `plan.parse_plan()`, `plan.expand_plan()` (Task 1), `mqtt_recorder.RecorderCore.set_recording_start()` (Task 5), `store.SessionWriter.update_segment()` (Task 4)
- Produces: 修改后的 recorder.py，运行后 h5 含 `meta/segments` + `/video/segment_idx` + `/video/state`

- [ ] **Step 1: 修改 recorder.py 主循环**

修改 [host/recorder/recorder.py](host/recorder/recorder.py)：

**line 34 后加 effective_plan 计算**：

```python
    plan_list = parse_plan(args.plan) if args.plan else []
    effective_plan = expand_plan(plan_list) if plan_list else []
```

**line 49 改 RecorderCore 注入（替换原 `RecorderCore(writer, on_event=...)`）**：

```python
    core = RecorderCore(writer, on_event=lambda k, v: print(f"[rec] {k}: {v}", flush=True),
                        effective_plan=effective_plan)
```

**gate 后、line 95 (`t0 = time.monotonic()`) 之前加 t0_wall_ns 注入**：

```python
    t0_wall_ns = time.time_ns()       # dev_doc/17 §4.5: 与 cam_capture 共享 wall-clock
    core.set_recording_start(t0_wall_ns)
    t0 = time.monotonic()
    last = t0
```

**主循环 line 107-117 替换 effective_plan 算术（替换原 plan_list 块）**：

```python
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
                            end_t_ns=t0_wall_ns + int((cum - seg.duration_s) * 1e9),
                            name=prev.name,
                            state=prev.state,
                        )
                    # 开启 NEW segment 的范围
                    cur = effective_plan[new_seg_idx]
                    main._seg_start_t_ns = t0_wall_ns + int((cum - seg.duration_s) * 1e9)
                    main._last_seg = new_seg_idx
                    print(f"[rec] segment {new_seg_idx + 1}/{len(effective_plan)} -> "
                          f"{cur.name} ({cur.state})", flush=True)
```

**finally 块 line 124-130 末尾加最后段关闭**：

```python
    finally:
        # dev_doc/17 §4.3: 关闭最后一段的范围
        if effective_plan and hasattr(main, "_last_seg") and hasattr(main, "_seg_start_t_ns"):
            last = effective_plan[main._last_seg]
            writer.update_segment(
                start_t_ns=main._seg_start_t_ns,
                end_t_ns=time.time_ns(),
                name=last.name,
                state=last.state,
            )
        if args.plan:
            writer.set_meta("plan", args.plan)
        client.loop_stop()
        writer.set_meta("recorder_status", str(core.status()))
        writer.close()
        print(f"[rec] Ended: frames={core.frames} crc_drops={core.crc_drops} -> {path}",
              flush=True)
```

**顶部 import 加 expand_plan**（line 19 后）：

```python
from plan import parse_plan, expand_plan
```

- [ ] **Step 2: 语法检查**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python -m py_compile host/recorder/recorder.py
echo $?
```

Expected: 0 (exit code)

- [ ] **Step 3: --help 确认无 CLI 改动**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python host/recorder/recorder.py --help 2>&1 | grep -E "transition|plan"
```

Expected: 看到 `--plan` 但无 `--transition-s`（已砍）

- [ ] **Step 4: Commit**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
git add host/recorder/recorder.py
git commit -m "feat(host/recorder): effective_plan 算术 + segment 范围表写入 (dev_doc/17 §4)"
```

---

## Task 7: cam_capture.py — PlanState 用 effective_plan + skeleton gating + overlay 分支

**Files:**
- Modify: `host/capture/cam_capture.py`

**Interfaces:**
- Consumes: `plan.parse_plan()`, `plan.expand_plan()`, `plan.PlanState()` (Task 2), `plan.draw_overlay_transition()` (Task 3)
- Produces: 修改后的 cam_capture.py，cam preview 在 transition 段隐藏骨骼、显示亮品红 overlay

- [ ] **Step 1: 修改 cam_capture.py import**

修改 [host/capture/cam_capture.py](host/capture/cam_capture.py) 顶部 import 区：

找到当前 `from plan import ...` 那行（应已存在 `parse_plan`），扩展为：

```python
from plan import parse_plan, expand_plan, PlanState
```

- [ ] **Step 2: 修改 plan_state 构造**

找到原 `plan_state = PlanState(plan=plan_list) if plan_list else None` 类似的代码行，替换为：

```python
plan_list = parse_plan(args.plan) if args.plan else []
effective_plan = expand_plan(plan_list) if plan_list else []
plan_state = PlanState(segments=effective_plan) if effective_plan else None
```

- [ ] **Step 3: 修改 main loop 加 `is_action` 判定 + overlay 分支**

找到主循环里：
```python
# overlay (preview only, gate by args.overlay)
if plan_state is not None and args.overlay:
    draw_overlay(preview, plan_state, elapsed)
```

替换为：

```python
# dev_doc/17 §5.3: skeleton gating 用 is_action
is_action = plan_state is None or plan_state.cur_state == "action"

# overlay 分支（preview only, gate by args.overlay）
if plan_state is not None and args.overlay:
    if is_action:
        draw_overlay(preview, plan_state, elapsed)
    else:
        draw_overlay_transition(preview, plan_state, elapsed)
```

找到原 skeleton 块：
```python
if args.skeleton and skel_runner is not None:
    if skel_frame_idx % SEARCH_DET_EVERY == 0:
        ...
```

在最外层 `if` 加 `is_action` 守卫：

```python
if args.skeleton and skel_runner is not None and is_action:
    if skel_frame_idx % SEARCH_DET_EVERY == 0:
        dets = skel_runner.detect(frame)
        skel_bboxes = [d[:4] for d in dets if d[4] >= SKEL_DET_THR][:MAX_PERSONS]
    for bbox in skel_bboxes:
        kpts = skel_runner.pose(frame, bbox)
        if kpts[:, 2].mean() >= KPT_THR:
            persons.append(kpts)
    skel_frame_idx += 1
    if persons:
        preview = skel_draw(preview, k[:, :, :2], k[:, :, 2],
                            openpose_skeleton=False, kpt_thr=KPT_THR)
    cv2.putText(preview, hud, (8, preview.shape[0] - 8), ...)
```

**关键**：skeleton 块完全用 `is_action` 守卫，transition 期间 detect/pose/draw_skeleton 都不跑（节省 ~25ms/帧）。左下角 HUD 也只在 action 段出现。

- [ ] **Step 4: 语法检查**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python -m py_compile host/capture/cam_capture.py
echo $?
```

Expected: 0

- [ ] **Step 5: --help 确认无 transition-s 旗标**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python host/capture/cam_capture.py --help 2>&1 | grep -E "transition|plan"
```

Expected: 看到 `--plan` 但无 `--transition-s` / `--transition-color`

- [ ] **Step 6: Commit**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
git add host/capture/cam_capture.py
git commit -m "feat(host/cam_capture): PlanState.effective_plan + skeleton gating + overlay 分支 (dev_doc/17 §5)"
```

---

## Task 8: test mode 端到端验证（dev_doc/17 §10.0 gate）

**Files:**
- No source code change
- Verify: `host/boot_recording.sh` test mode（已在 round 3 改为 `1:stand:30,2:squat:30`）

**Pre-flight:**
- 设备就绪：3 块 ESP32 RX 板插 USB（`/dev/ttyACM{0,1,2}` 在）
- webcam：`/dev/video0` 在
- mosquitto 运行中
- dac_dev env 已激活

- [ ] **Step 1: 跑全测试套件**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python -m pytest host/tests/ -v
```

Expected: 全部 pass（test_plan_effective: 4, test_plan_state: 3, test_overlay_transition: 1, test_store_segment: 3, test_recorder_lookup: 5 = 16 passed）

- [ ] **Step 2: 跑 test mode 端到端**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
./host/boot_recording.sh test s01-r1
```

按 Enter 开始录制。**会录 70s**（不是 60s）。

- [ ] **Step 3: 验证 mp4 时长 ≈ 70s**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
ffprobe $(ls -t data/test/s01-r1-*.mp4 | head -1) -show_entries format=duration -of default=noprint_wrappers=1:nokey=1
```

Expected: ~70（70.0 ± 1.0）

- [ ] **Step 4: 验证 h5 `/meta/segments` 含 3 段**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python -c "
import h5py, json, sys
files = sorted(__import__('glob').glob('data/test/s01-r1-*.h5'))
assert files, 'no h5 found'
h = h5py.File(files[-1], 'r')
segs = json.loads(h['meta'].attrs['segments'])
print(json.dumps(segs, indent=2, ensure_ascii=False))
assert len(segs) == 3, f'expected 3 segments, got {len(segs)}'
assert [s['state'] for s in segs] == ['action', 'transition', 'action']
assert [s['name'] for s in segs] == ['stand', 'transition', 'squat']
print('OK: 3 segments, action/transition/action')
"
```

Expected:
```json
[
  {"start_t_ns": ..., "end_t_ns": ..., "name": "stand", "state": "action"},
  {"start_t_ns": ..., "end_t_ns": ..., "name": "transition", "state": "transition"},
  {"start_t_ns": ..., "end_t_ns": ..., "name": "squat", "state": "action"}
]
OK: 3 segments, action/transition/action
```

- [ ] **Step 5: 验证 h5 `/video/state` 标记正确**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python -c "
import h5py, numpy as np
files = sorted(__import__('glob').glob('data/test/s01-r1-*.h5'))
h = h5py.File(files[-1], 'r')
state = h['video/state'][...]
n_frames = len(state)
n_action = int((state == 1).sum())
n_trans = int((state == 0).sum())
print(f'total frames: {n_frames}, action: {n_action}, transition: {n_trans}')
# 70s @ ~20fps = ~1400 frames; transition 应 ~10s ~ 200 frames
assert n_trans > 100, f'too few transition frames: {n_trans}'
assert n_action > n_trans, 'action frames should dominate'
print('OK: state 标记合理')
"
```

Expected: total frames 几百到一千多，action 占多数，transition ≥100 帧

- [ ] **Step 6: cam preview 视觉验证（手动）**

观察 test mode 录制时的 cam 窗口：
- 0-30s：右上角黄色 box "Segment 1/3 — stand / ● RECORDING 0.0s / 30.0s"，骨骼可见
- 30-40s：**右上角亮品红 box + 白字** "Transition 1/1 — TRANSITION / ● RECORDING 0.0s / 10.0s"，**骨骼消失**
- 40-70s：右上角黄色 box "Segment 3/3 — squat / ● RECORDING 0.0s / 30.0s"，骨骼可见

**4 项 gate 全绿后才能进 Task 9**（dev_doc/17 §10.0 强制门禁）。

- [ ] **Step 7: 标记完成 + 不 commit（验证步骤无需 commit）**

```bash
echo "test mode 4 gates all green"
```

---

## Task 9: norm mode 端到端验证（test 全绿后才进）

**Files:**
- No source code change
- Verify: `host/boot_recording.sh` norm mode + 11 transitions

- [ ] **Step 1: 跑 norm mode 端到端**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
./host/boot_recording.sh norm s01-r1
```

按 Enter 开始录制。**会录 700s**（不是 580s）。

- [ ] **Step 2: 验证 mp4 时长 ≈ 700s**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
ffprobe $(ls -t data/s01-r1-*.mp4 | head -1) -show_entries format=duration -of default=noprint_wrappers=1:nokey=1
```

Expected: ~700（700.0 ± 5.0）

- [ ] **Step 3: 验证 h5 `/meta/segments` 含 25 段（13 action + 12 transition）**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python -c "
import h5py, json
files = sorted(__import__('glob').glob('data/s01-r1-*.h5'))
h = h5py.File(files[-1], 'r')
segs = json.loads(h['meta'].attrs['segments'])
print(f'total: {len(segs)} segments')
print(f'action: {sum(1 for s in segs if s[\"state\"] == \"action\")}')
print(f'transition: {sum(1 for s in segs if s[\"state\"] == \"transition\")}')
assert len(segs) == 25, f'expected 25, got {len(segs)}'
assert sum(1 for s in segs if s['state'] == 'transition') == 12
print('OK: 25 segments, 12 transitions')
"
```

Expected: 25 段 / 13 action / 12 transition

- [ ] **Step 4: cam preview 视觉验证（手动）**

观察 norm mode 录制时的 cam 窗口：
- 60s / 100s / 140s / ... / 700s 这些 boundary 处应有亮品红 overlay（12 处）
- action 段应有黄色 overlay + 骨骼
- transition 段应只有品红 box + 骨骼消失

- [ ] **Step 5: backward compat 验证（fall-demo-01 等旧 h5）**

```bash
cd /home/ruo/Desktop/LYX/USTB-SONY/esp-csi-v2/ESP32_FallRec_Reference/ReferenceCode/Opensourse/csi-pose
/home/ruo/anaconda3/envs/dac_dev/bin/python -c "
import h5py
# 找一个旧 h5 (可能是 data/fall-demo-01-*.h5 或更早的)
import glob
old_h5 = glob.glob('data/*/fall-demo-*.h5') or glob.glob('data/fall-demo-*.h5')
if not old_h5:
    print('SKIP: 无旧 h5 可测')
else:
    h = h5py.File(old_h5[0], 'r')
    has_segments = 'segments' in h['meta'].attrs
    has_seg_idx = 'video/segment_idx' in h
    print(f'old h5: {old_h5[0]}')
    print(f'  has meta/segments: {has_segments} (期望 False)')
    print(f'  has video/segment_idx: {has_seg_idx} (期望 False)')
    print('OK: 旧 h5 无新字段, trainer fallback 路径正确')
"
```

Expected: 旧 h5 无 `meta/segments` / `video/segment_idx` → trainer fallback 路径可用

- [ ] **Step 6: 标记全部完成**

```bash
echo "transition feature: test + norm end-to-end OK"
```

---

## Self-Review Checklist

**1. Spec coverage** — 核对 dev_doc/17 的每条需求是否都有 task 覆盖：

| Spec 章节 | 覆盖 task |
|---|---|
| §1 决策汇总 9 条 | Task 1-7 全覆盖 |
| §2.2 PlanSegment + expand_plan | Task 1 |
| §2.3 PlanState 重构 | Task 2 |
| §3.4 SessionWriter 扩展 | Task 4 |
| §4.4-§4.5 RecorderCore 扩展 | Task 5 |
| §4.3 recorder.py 主循环 | Task 6 |
| §5.3 cam_capture skeleton gating | Task 7 |
| §5.4 draw_overlay_transition | Task 3 |
| §6 boundary 11 个 | Task 1 单测 + Task 9 验证 |
| §7 boot_recording.sh | 已 round 3 完成 |
| §8 --duration auto | Task 6 (recorder) + Task 7 (cam_capture 不传 --duration 由 effective_plan 自算) |
| §9 backward compat | Task 8 (test 4 gate) + Task 9 (norm + 旧 h5 fallback) |
| §10.0 test-mode-first gate | Task 8 (先 test) → Task 9 (后 norm) |
| §10.1-§10.4 单测 | Task 1-5 各自的 pytest |

无遗漏。✅

**2. Placeholder scan** — 检查 plan 中是否有"TBD / TODO / 类似 / 后续"等占位符：

- 无 "TBD"
- 无 "TODO"
- 无 "implement later"
- 无 "Similar to Task N"（每步都给了完整代码）
- 无 "appropriate error handling"（直接给了代码）

✅

**3. Type consistency** — 跨任务类型/方法签名一致性：

| Task 1 定义 | 后续 task 使用 | 一致？|
|---|---|---|
| `expand_plan(plan: list) -> list[PlanSegment]` | Task 2/5/6/7 全部用 `expand_plan(plan_list)` | ✅ |
| `PlanSegment(idx, name, duration_s, state)` | Task 2/5/7 全部用 `.name / .duration_s / .state` | ✅ |
| `PlanState(segments=...)` | Task 7 用 `PlanState(segments=effective_plan)` | ✅ |
| `PlanState.cur_state / .cur_label / .cur_duration` | Task 3 (draw_overlay_transition) 用 .cur_duration 和 .seg_start | ✅ |
| `RecorderCore(writer, *, on_event=None, effective_plan=None)` | Task 6 用 `RecorderCore(writer, on_event=..., effective_plan=effective_plan)` | ✅ |
| `RecorderCore.set_recording_start(t_wall_ns)` | Task 6 在 gate 后调 | ✅ |
| `RecorderCore._lookup_segment(t_ns) -> tuple[int, int]` | Task 5 单测断言 seg_idx, state 位置 | ✅ |
| `SessionWriter.append_video(t_ns, frame_idx, *, seg_idx=0, state=1)` | Task 5 `_on_cam` 用 keyword args | ✅ |
| `SessionWriter.update_segment(*, start_t_ns, end_t_ns, name, state)` | Task 6 用 keyword args | ✅ |

无类型不一致。✅

---

## Execution Notes

- **commit message 前缀**：`feat(host/...): ...` / `refactor(host/...): ...` / `feat(host/tests): ...`
- **每 task 结束应 git status 干净**（除已 commit 的文件）
- **TDD 严格执行**：先 failing test，再 minimal impl，再 verify pass
- **若 Task 4 step 5 检测到 mqtt_recorder 旧调用断**（unlikely，append_video 关键字参数兼容），立即 commit 当前 task 后转 Task 5 修复
- **Task 8/9 失败** → 按 CLAUDE.md §9.1 连续 5 报错机制：先复现，再读 dev_doc/17 对应章节核对逻辑，禁止盲目补丁