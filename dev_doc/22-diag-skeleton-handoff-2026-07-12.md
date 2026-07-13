# 22-diag-skeleton-handoff-2026-07-12

> **状态**：上一会话已做完的修复**未真正生效**，用户 2026-07-12 反馈"目前跑视频都无法正常绘制骨骼"。交接给下一个 agent。

---

## 1. 任务背景

在 `runs/s01-rX-norm/best.pt`（in_ch=560, features=phase+rssi, PCK@0.5=78.9%）上跑 `rt/demo.py --replay --diag-fill-missing --video --save demo_diag.mp4`，期望 mp4 里能看见火柴人。

## 2. 用户报告（最新）

**"目前跑视频都无法正常绘制骨骼"** —— 修复**没有真正解决**问题，需要继续。

## 3. 已尝试的修复（可能未生效）

### 3.1 代码改动（已写入磁盘）

| 文件 | 改动 | 假设 |
|---|---|---|
| [rt/csi_rt/infer.py](rt/csi_rt/infer.py) | 加 `_DIAG_FAKE_XY` (T-pose placeholder 18 joints 归一化坐标) + `_DIAG_FAKE_C = 0.5`；`__call__` 在 `self.diag` 时跳过 collapse-xy，直接 return placeholder | xy 坍缩到 ±5,±13 → project clip 后堆角落 → 替换 placeholder 即可 |
| [rt/csi_rt/overlay.py](rt/csi_rt/overlay.py) | banner `DIAG` 后缀 + 灰色对角线 + `PRED-INVALID` 字幕 | 让画面诚实标记 |
| [rt/demo.py](rt/demo.py) | hud 加 `"diag": self.est.diag` 透传 | 让 banner 拿到 diag 标志 |

### 3.2 mp4 产物

- `runs/s01-rX-norm/demo_diag.mp4` (101MB) — v2 placeholder 版本，**用户确认仍不显示**
- `runs/s01-rX-norm/demo_diag_v1_buggy.mp4` (71MB) — v1 角落对角线版本

### 3.3 上一会话的 probe 结论（已写入 dev_doc/21 §3.7）

- Real xy range: x=[-3.4, 5.4], y=[-13.8, 1.7]（vs placeholder (0,1) 范围）
- Confidence mean=10（远超 gate 0.3，present=True 没问题）
- 替换后 placeholder 也被画到画布中央（v2 截图视觉确认 OK）

## 4. 下一个 agent 接手要点

### 4.1 用户原话（最新）

> "没有成功，目前跑视频都无法正常绘制骨骼"

### 4.2 不要假设 placeholder 修复有效

dev_doc/21 §3.7 写的"修复成功"是**针对 demo_diag 单跑**的视觉验证（看一帧画面 OK）。但用户用**某种方式重新跑视频**后**仍然看不到骨骼**。可能原因（按可能性排序）：

1. **用户复跑未生效**：可能没删 `_v1_buggy.mp4` / 没重跑 / 命令参数不对
2. **mp4 写入路径错了**：v2 重渲染时 writer 用的是 `cv2.VideoWriter(args.save, ..., 20, ...)` 但 fps=20 与 replay rate 不匹配
3. **Engine.tick 中 `xy` 真的传成 None**：demo.py `_last` 初值 `(None, None, False, "IDLE")`，早期帧 `cut.valid` 为 False 时不更新 → `_draw_skeleton` 不会画
4. **placeholder 写错了**：`_DIAG_FAKE_XY` 拷到 `infer.py` 时可能错位（joint index 与 EDGES 不对应）
5. **画布坐标系问题**：CANVAS_WH = (640, 480) 而 video 是 1280×720 → cv2.resize 后比例不对，但骨架还是能画

### 4.3 必做的下一步

1. **重跑命令全文记录** — 让用户贴出实际跑的命令 + 输出的关键日志
2. **读 `runs/s01-rX-norm/demo_diag.mp4` 当前帧实际像素** — 用 Read tool 看 PNG 抽帧，确认是否真有火柴人
3. **检查 `infer.py` placeholder 是否真的被读到**：`_DIAG_FAKE_XY` import 是否成功 / `self.diag` 是否真的 True / `__call__` 是否真的走到 `if self.diag:` 分支
4. **检查 `demo.py --save`**：是否真的写到磁盘、writer 是否成功 open、writer.release 是否被调用

### 4.4 不要做的事

- ❌ 不要继续重写 placeholder 形状（T-pose vs 其它）的视觉 hack
- ❌ 不要新增更多可视化标记 / X-watermark / 字幕
- ❌ 不要扩 `--diag-*` flag 数量

### 4.5 可能的真实 fix 方向（待用户确认）

- **方向 A**：跑过 demo 但用户期望的"看到骨骼"是指**真实预测**而非 placeholder —— 此时必须把 phase/rssi 接到 ReplaySource/LiveSource（dev_doc/21 §4.3，dev_doc/20 §5.3）才能看到真骨骼
- **方向 B**：跑过 demo 但 mp4 帧出错 —— 检查 writer / encoder / drop 帧问题
- **方向 C**：跑 demo 但条件不对（比如 `--video` 没传）—— 此时只有 canvas 单边显示

## 5. 当前真实文件状态（核对起点）

```
runs/s01-rX-norm/
├── best.pt                                 # 38MB, in_ch=560, features=phase+rssi
├── demo_diag.mp4                           # 101MB, v2 fix (placeholder 骨架)
├── demo_diag_v1_buggy.mp4                  # 71MB, 角落对角线版本
├── log.jsonl                               # 30 epoch 训练曲线
├── perf-diag.json                          # rt 性能
└── report-val.json                         # eval PCK
```

修改后的代码：
- `rt/csi_rt/infer.py` 包含 `_DIAG_FAKE_XY` 常量 + diag 替换逻辑
- `rt/csi_rt/overlay.py` 含 ` DIAG` banner + 灰色斜线 + 字幕
- `rt/demo.py` hud 多 `"diag": self.est.diag`

## 6. 一句话

dev_doc/21 §3.7 自报"修复成功"是**视觉抽帧确认的假阳性**，用户复跑仍未生效。**停止再加修改**，先回到用户现场重跑命令 + 抽帧，确认是 placeholder 没生效还是用户期望的真预测语义。

---

**最后更新**：2026-07-12 by Claude (handoff)
**依据**：用户最新反馈 "没有成功，目前跑视频都无法正常绘制骨骼" + "用最简短的话语书写一份handoff文件"
