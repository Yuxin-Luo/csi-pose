# 16 — cam_capture.py 实时骨骼预览

**日期**：2026-07-12
**状态**：✅ 已实现、syntax OK、合成帧端到端验证通过；GUI 实拍待用户在 Phase 1b 长跑中确认
**改的文件**：[host/capture/cam_capture.py](host/capture/cam_capture.py) (1 个文件，~100 行新增)
**触发需求**：
1. 用户发现 cam 窗口没有骨骼，无法提前确认录制视频能否被 RTMPose 识别
2. 用户对帧率感兴趣（"det 显示延迟在 90-140ms 之间"、"fps 从 30 掉到 10"）
3. 用户要求 **mp4 = 100% raw**，所有 cv2 文字（包括之前妥协留下来的 segment overlay）一律只画在 preview 上
4. fps 显示移到 preview 左上角

---

## 1. 设计决策

| 决策 | 选项 | 选定 | 依据 |
|---|---|---|---|
| 默认开关 | ON / OFF | **ON**（`--no-skeleton` 关）| 用户明确要求"默认开启，但可以手动关闭" |
| mp4 内容 | 含文字 / 100% raw | **100% raw**（包括 segment overlay）| 用户决定按作者原意："作者没有加文字我们也保存为 100% raw"。所有 cv2.putText/draw_skeleton/draw_overlay 操作 preview-only |
| 检测模式 | search-track / 持续 detect | **search mode**：每 5 帧 detect | 640×360 cam，detect=~94ms / pose=~7.5ms（实测），detect 每帧会拖垮 fps |
| 模型路径 | 自定义 / 复用 | **复用** `teacher.csi_teacher.runner.make_runner` | 与 `live_skeleton.py` 和 `teacher.py` 同一对 RTMDet-m + RTMPose-m，行为一致 |
| 推理设备 | cpu / cuda / auto | **cpu** 默认（CLAUDE.md §7 速率红线：本机 RTX 不强，CUDA 路径未测） | 实测 CPU 30fps 跑得下 |
| runner 初始化时机 | 模块级 / lazy | **lazy**（按 gate 后）| 不让用户在按 Enter 前等 ~145MB 模型下载 |
| Preview/Frame 分离 | 别名 (`preview = frame`) / 拷贝 | **`preview = frame.copy()`** | 别名方案下 cv2.putText(preview,...) 会 in-place 修改 frame 本体（虽然 writer.write 已先调用过，但极易在后续 refactor 中漏掉 → 踩坑）。拷贝方案在 `--no-skeleton` 路径同样安全 |
| fps 显示位置 | 顶/底/边 | **左上角**（黄色 `FPS XX.X`，恒定显示）| 用户明确要求"页面左上角" |
| live fps 统计窗口 | 累计平均 / 滑动窗口 | **滚动 1s 窗口**（每 LIVE_FPS_WINDOW_S=1s flush 一次）| 累计均值启动后第 1 分钟都偏低；窗口均值更快反映瞬时 fps |

---

## 2. 关键实现点

### 2.1 lazy init (post-gate)

[cam_capture.py:212-241](host/capture/cam_capture.py#L212-L241) — 在 `cv2.waitKey(0)` 按键后才下载模型。

```python
if args.skeleton:
    try:
        from rtmlib import draw_skeleton as _draw_skeleton
        skel_draw = _draw_skeleton
    except ImportError as e:
        print(f"[cam] ERROR: --skeleton requires rtmlib ({e.name}). ...")
        sys.exit(1)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "teacher"))
        from csi_teacher.runner import make_runner
        skel_runner = make_runner(device=args.skeleton_device)
    except Exception as e:
        print(f"[cam] ERROR: skeleton runner init failed: {e}")
        sys.exit(1)
```

**为什么 sys.exit(1) 而不是 fallback 到 --no-skeleton**：失败原因大概率是 rtmlib 没装，留着"半残"状态反而难调试。

### 2.2 mp4 不被污染（hardened，2026-07-12 加固）

[cam_capture.py:~308-368](host/capture/cam_capture.py#L308-L368) — `preview = frame.copy()` + `writer.write(frame)` 顺序保证 mp4 = 100% raw frame：

```python
core.handle_frame(t)
writer.write(frame)        # mp4 = 100% raw (frame never touched by cv2)

# All cv2 below operates on preview only
preview = frame.copy()

# Top-left: live fps（恒定显示）
cv2.putText(preview, f"FPS {live_fps:4.1f}", (8, 28), ...)

# Top-right: segment overlay（仅 preview，gate by args.overlay）
if plan_state is not None and args.overlay:
    draw_overlay(preview, plan_state, elapsed)

# 骨骼 + 左下角 HUD（仅 preview，gate by args.skeleton）
if args.skeleton and skel_runner is not None:
    if persons:
        preview = skel_draw(preview, k[:, :, :2], k[:, :, 2],
                            openpose_skeleton=False, kpt_thr=KPT_THR)
    cv2.putText(preview, hud, (8, preview.shape[0] - 8), ...)

cv2.imshow("cam", preview)
```

**关键不变量**（合成帧端到端测试通过）：
- `writer.write(frame)` 之后，再无任何 cv2 调用触碰 `frame` 本体
- `preview = frame.copy()` 断开别名，`cv2.putText(preview, ...)` 不可能污染 mp4
- 即使 `--no-skeleton` 路径也走同样的 copy + putText（fps HUD 恒定显示）

**为什么不用 `preview = frame` 别名写法**：别名的瞬间，`cv2.putText(preview, ...)` 会 in-place 修改 frame 本体。当前的代码靠"writer.write 在前"保平安，但极容易在后续 refactor 中被踩坑（任何把 putText 提前到 writer.write 之前的 PR 都会无声污染 mp4）。`frame.copy()` 让这件事"形式上不可能发生"。

### 2.3 search-mode 调度（**已修复 bug**：2026-07-12）

[cam_capture.py:~344-360](host/capture/cam_capture.py#L344-L360) — 不复用 `live_skeleton.py` 的 search/track 2-mode 完整版（cam_capture 是短时录制，track-mode bbox 继承意义不大），简化为：detect 每 5 帧，pose 每帧。

```python
# Search mode: detect every SEARCH_DET_EVERY frames, PERIODIC.
# No "if not skel_bboxes" fallback — that would detect every frame when no
# one is in shot and pin fps at ~10 (RTMDet ~100ms/frame). Upstream
# live_skeleton.py uses pure time-based _det_due() for the same reason.
if skel_frame_idx % SEARCH_DET_EVERY == 0:
    dets = skel_runner.detect(frame)
    skel_bboxes = [d[:4] for d in dets if d[4] >= SKEL_DET_THR][:MAX_PERSONS]

for bbox in skel_bboxes:                      # 每帧都 pose (7.5ms 便宜)
    kpts = skel_runner.pose(frame, bbox)
    if kpts[:, 2].mean() >= KPT_THR:
        persons.append(kpts)
```

#### Bug history（2026-07-12 现场实拍发现）

**症状**：无人在画面时 fps ≈ 9.7；人进画时 fps ≈ 15-17（看似应降反升）。

**根因**：初版写成 `if not skel_bboxes or skel_frame_idx % SEARCH_DET_EVERY == 0:`，当 `skel_bboxes == []` 时 `or` 短路触发 detect **每帧**：

| 场景 | detect 调用频率 | 帧开销 |
|---|---|---|
| 无人在画面（bug）| 100% 帧 | detect 100ms + 其他 ~10ms ≈ **110ms/帧 → fps 9** |
| 人在画面（bug）| 每 5 帧 | 100ms detect / 5 + 20ms pose + 其他 ≈ **~50ms/帧 → fps 20** |

结果：用户看到 fps 在有人时反而"升"到 15-17（误以为神秘），没人时反而更低。

**对比上游**：[live_skeleton.py:64-68](host/tools/live_skeleton.py#L64-L68) 用 `_det_due(now)` 纯时间触发：

```python
def _det_due(self, now):
    if not self.tracking:
        return self._frame_idx % SEARCH_DET_EVERY == 0   # ← 纯 modulo，无 bbox 短路
    return now - self._last_det_t >= TRACK_DET_PERIOD
```

作者故意没加 "empty bbox → 立即重 detect" 的 fallback——理由相同：会让 RTMDet 在没人时陷入 detect 风暴。

**修复**：直接把 `not skel_bboxes or` 砍掉，保留纯 modulo。

**回归测试**：合成 40 帧 + stub `detect` 总是返回 `[]`：
- 修前（带短路）：detect 被调 40 次
- 修后（纯 modulo）：detect 被调 8 次 == `40 // SEARCH_DET_EVERY` ✅（已实跑通过）

**预期新行为**：
- 无人在画面：detect 每 5 帧 + pose 空 → fps 期望 ~25-30（受 USB/写盘/显式 cap 影响可能落 20-25）
- 人在画面：detect 每 5 帧 + pose 每帧 20ms × N bbox → fps 期望 ~15-20

人刚出/进帧时 ≤4 帧的 stale bbox 会被 `kpts[:,2].mean() >= KPT_THR` 过滤掉，骨头短暂消失 167ms（≤5 帧 at 30fps），预览无感。

---

## 3. 实测数据（dac_dev env, CPU only, 640×360 合成帧）

| 阶段 | 延迟 |
|---|---|
| `runner.detect` (RTMDet-m @ 640×640) | **median 93.6ms** (min 93, max 115) |
| `runner.pose` (RTMPose-m @ 192×256) | **median 7.5ms** |
| `draw_skeleton` (rtmlib) | < 1ms |

**有效 fps 预算**（detect 5 帧一次 + pose 每帧 + cv2 grab/write/imshow）：
- 每帧均摊推理 = 93.6/5 + 7.5 = **26.2ms**
- + grab (~5ms) + imshow (~2ms) + write (~5ms) ≈ **38ms/帧** → ~26fps

**实测 mp4 帧率**：取决于 cam USB2 实际吞吐。640×360 @ 30fps 已验证（dev_doc/12），加 26ms 推理后理论 26fps 仍 ≥25fps 目标。

---

## 4. 使用方式

```bash
# 默认 (--skeleton on) —— 推荐, 录的时候能看到骨骼
./host/boot_recording.sh norm s01-r1

# 关掉 (跟以前一样)
./host/boot_recording.sh norm s01-r1   # → 等价：需要改 cam_capture 调用
                                       # 当前 boot_recording.sh 调用没有传 --skeleton/--no-skeleton
                                       # → 默认 ON

# 直接调 cam_capture 时
python host/capture/cam_capture.py --session test --duration 30 --no-skeleton
python host/capture/cam_capture.py --session test --duration 30 --skeleton-device cuda
```

**注意**：cam 是 single-occupancy（USB2 MSMF 后端），不能同时跑 `host/tools/live_skeleton.py` + `boot_recording.sh`。本特性把 live_skeleton 能力内联到 cam_capture，一个窗口解决。

---

## 5. 依赖 & 一次性安装

dac_dev env 需要：

```bash
/home/ruo/anaconda3/envs/dac_dev/bin/pip install --no-deps rtmlib==0.0.15 onnxruntime tqdm
```

首次 `--skeleton` 启动会自动下载：
- `rtmdet_m_8xb32-100e_coco-obj365-person-235e8209.onnx` (97MB)
- `rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0.onnx` (48MB)

缓存路径 `~/.cache/rtmlib/hub/checkpoints/`，**第二次启动秒开**。

---

## 6. 验证状态

| 项 | 状态 | 方法 |
|---|---|---|
| 语法检查 | ✅ | `python -m py_compile` |
| argparse 兼容性 | ✅ | `--help` 显示新 flag；`--no-skeleton`/`--skeleton`/`--skeleton-device` 都 parse OK |
| 模块导入 | ✅ | `spec.loader.exec_module()` OK |
| runner 集成（合成帧） | ✅ | detect+pose+draw_skeleton 全链路在合成 640×360 帧上跑通，无 crash |
| **mp4 100% raw 不变量**（强化后）| ✅ 验证 | 合成帧 stub：mp4 写入数组 == 入帧原始 byte；preview 数组 != 原始 frame（HUD 已绘）。`preview = frame.copy()` 切断别名 |
| **detect 触发是纯 modulo（修复 bug）**| ✅ 验证 | 修前 detect 在空 bbox 时被短路成"每帧"，把 fps 锁死在 9.7；修后 detect 调用次数 == `frames // SEARCH_DET_EVERY`（合成 stub 实测 40 帧 → 8 次）。上游 `live_skeleton.py:_det_due()` 也是纯 modulo 设计 |
| GUI 实拍（有人） | ⏸ 待用户确认 | 用户当前正在 Phase 1b 长跑，下一轮按 Enter 后能看到 |
| mp4 干净抽帧验证（无人/有人都要） | 待用户确认 | 跑完用 `python -c "import cv2; cap=cv2.VideoCapture('data/s01-r1-...mp4'); ..."` 抽帧，确认无任何 cv2 文字、无段标、无骨骼、无 bbox |
| **模型一致性** | ✅ 验证 | 预览和 teacher.py 都走 `teacher.csi_teacher.runner.make_runner`，ONNX 路径、阈值、输入尺寸全相同；预览比 QA 多一道 kpt_thr=0.3 显示过滤（更保守），h5 存的全部 keypoints 一定 ≥ 预览显示 |
| **fps 左上角 HUD** | ✅ 实现 | 黄色 `FPS XX.X`，恒定显示，无论 `--skeleton` 还是 `--no-skeleton` |

## 7. 给后续 agent 的提示

- **不要把 draw_skeleton 当 in-place 操作**：它返回新数组。误用会污染 preview 但不会污染 mp4（已在 `preview = frame.copy()` 切断别名）。
- **不要再回到 `preview = frame` 别名写法**：当前 `frame.copy()` 是 mp4 = raw 的形式化保证。任何 future refactor 都必须保留这层。
- **不要再在 frame 上调用 draw_overlay**：用户在 2026-07-12 决定 mp4 = 100% raw（按作者原意）。所有 cv2 文字一律走 preview。
- **不要在 `import` 顶层 import rtmlib**：cam_capture 可能被 `host/tests/*` import（不让 rtmlib 缺失导致单测失败）。
- **不要复用 live_skeleton.py 的 2-mode 完整版**：cam_capture 的预览窗口寿命短（最长 580s），track-mode bbox 继承得不偿失。
- **detect 阈值 (SKEL_DET_THR=0.5) 和 pose 阈值 (KPT_THR=0.3) 与 live_skeleton.py 一致**——保证 teacher.py 离线打标签和预览结果一致。如果用户调了其中一个，记得同步另一个。
- **live_fps 统计只在 `if ret:` 块内累计**：掉帧不计数；与 `[cam] ... fps_live=...`（每 status_period 5s）的含义一致——都是"成功处理的帧率"。

---

**维护者**：Claude
**依据**：handoff §4.2 + 本次实测 CPU fps 数据 + 用户反馈"担心录制完视频后无法正常识别骨骼"+"mp4 = 100% raw，按作者原意"+"fps 显示在左上角"