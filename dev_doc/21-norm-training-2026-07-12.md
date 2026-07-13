# 21-norm-training-2026-07-12

> **状态**：norm 模式（13 段 580s）训练**端到端跑通**，best PCK@0.2 = **36.5%** / PCK@0.5 = **78.9%** / mpjpe = **37 px**。
> 对比 test 模式 (60s 757 samples) PCK@0.2 = 1.98% → norm PCK@0.2 = 36.5%，**18× 提升**，验证 dev_doc/20 §4.1 数据量根因。

---

## 1. 目标

把 dev_doc/20 验证过的 pipeline 在 norm 13 段数据上跑通，并对比 PCK 是否跨过有效阈值。

## 2. 数据

| 字段 | 值 |
|---|---|
| 会话 | `s01-rX`（norm 模式 580s，13 段 D1 计划）|
| H5 | `data/s01-rX-20260712-164531.h5` (66MB) |
| MP4 | `data/s01-rX-20260712-164551.mp4` (39MB, 9646 frames @ 21.3fps) |
| CSI frames | 497534 packets（9 个 link 累加）|
| Cam anchors | 9623 rows / 9616 presence (99.93%) / 7 discarded |
| Split | my_split.py (train 7651 / val 1876 / gap 96, 80:20 chronological) |

### 2.1 段时序

| 段 | 时长 (s) | state |
|---|---|---|
| 1: empty_in | 60 | action |
| 2: pos1_set1 | 40 | action |
| 3: pos2_set1 | 40 | action |
| 4: pos3_set1 | 40 | action |
| 5: pos1_set2 | 40 | action |
| 6: pos2_set2 | 40 | action |
| 7: pos3_set2 | 40 | action |
| 8: pos1_set3 | 40 | action |
| 9: pos2_set3 | 40 | action |
| 10: pos3_set3 | 40 | action |
| 11: sit | 40 | action |
| 12: lie_supine | 60 | action |
| 13: empty_out | 60 | action |

+ 11 transition (10s each) → effective plan = 25 segments / 700s

## 3. Pipeline 执行日志

### 3.1 teacher.py label (Step 1)

| 字段 | 值 |
|---|---|
| 命令 | `python teacher/teacher.py label data/s01-rX-20260712-164551.mp4 --h5 data/s01-rX-20260712-164531.h5 --device cuda` |
| 速度 | 41.9 fps (RTX 4060 + onnxruntime-gpu 1.19.2) |
| Frames | 9646 → ok=9639, no_person=0, multi=7 |
| 时长 | ~4 min |

**踩坑（dev_doc/20 §4.3 解开）**：
- onnxruntime 1.19.2 CPU-only build 不带 CUDAExecutionProvider
- 解决：`pip install onnxruntime-gpu==1.19.2` + `nvidia-cudnn-cu12/cufft/cublas/cuda_runtime/...` + `LD_LIBRARY_PATH` 指向 `site-packages/nvidia/*/lib`
- 一次解决，之后所有 ORT 调用都走 CUDA EP

### 3.2 build_samples (Step 2a)

| 字段 | 值 |
|---|---|
| 命令 | `python host/tools/build_samples.py --h5 data/s01-rX-20260712-164531.h5` |
| Clockfit | rx0/rx1/rx2 三个 board 同步（slope_ppm ≈ -29，resid_p50 ≈ 27.7ms）|
| G | 59864 (100Hz grid spans) |
| N | 9623 anchor windows (匹配 video frames) |
| valid | 42.4% (CSI 周期性 + 一些丢包) |

### 3.3 teacher.py pam (Step 2b)

| 字段 | 值 |
|---|---|
| 命令 | `python teacher/teacher.py pam --h5 data/s01-rX-20260712-164531.h5` |
| N | 9623 → presence=9616 (99.93%), discarded=7 |

### 3.4 train.fit (Step 3) — **核心结果**

| 字段 | 值 |
|---|---|
| 命令 | `python train/train.py fit --config configs/train-norm.yaml --loss-mode pam_full --rssi --phase --augment --device cuda --epochs 30 --name s01-rX-norm` |
| Config | batch=32, lr=1e-3, wd=1e-4, warmup=2, augment=4x |
| in_ch | 560 (amp 280 + phase 280, rssi rescale 隐式) |
| 数据 | train=7651 / val=1876 |
| 时长 | ~25 min on RTX 4060 |
| **best PCK@0.2** | **0.3647 (ep23)** |
| **best PCK@0.5** | **0.7880 (ep23)** |
| coord loss | 0.0467 → 0.0005 (94× ↓) |

**训练曲线**：

| ep | coord | PCK@0.2 | PCK@0.5 |
|---|---|---|---|
| 0 | 0.0467 | 0.002 | 0.014 |
| 6 | 0.0019 | 0.190 | 0.596 |
| 12 | 0.0010 | 0.263 | 0.707 |
| 18 | 0.0007 | 0.311 | 0.743 |
| 23 (best) | 0.0006 | **0.365** | **0.788** |
| 29 | 0.0005 | 0.358 | 0.787 |

### 3.5 train.eval (Step 4a)

| 字段 | 值 |
|---|---|
| 命令 | `python train/train.py eval --ckpt runs/s01-rX-norm/best.pt --config configs/train-norm.yaml --split val` |
| mpjpe_px | 37.0 px（640×360 图像上 10% 头身长以内）|
| mpjpe_norm | 0.0813 |
| kappa | 0.489 |
| n_eval | 725 / 1876 (valid rows) |

**Per-joint PCK@0.2**（看哪些关节难）：
```
joint 0 (nose):    0.124
joint 1-3 (eye/ear): 0.13-0.18
joint 4-7 (shoulder/elbow): 0.16-0.25
joint 8-9 (wrist):  0.27-0.38  ← 末端肢体最好
joint 10-13 (hip/knee): 0.42-0.61
joint 14-17 (ankle/foot): 0.53-0.73  ← 腿末端最好
```

> 末端肢体（手腕/脚踝）准确率远高于面部点，因为 CSI 对大尺度肢体运动更敏感。

### 3.6 rt/demo.py 实时推理 (Step 4b/c)

**用作者原版** `rt/demo.py --replay --ckpt --video --fast --headless`：

| 字段 | 值 |
|---|---|
| 命令 | 见下 |
| frames_in | 497534 (CSI packets) |
| windows | 11972 (50ms cuts) |
| valid_windows | 5082 |
| **infer_ms_p50** | **16.7ms** |
| **infer_ms_p95** | **17.3ms** |
| fps_mean | 91.1 (replay 速率) |
| alarms | 0 (squat/sit/lie 不触发 IMPACT FSM) |

**性能**：
- 50ms tick 预算内（17ms << 50ms）
- 24 fps 实时可能（50ms - 17ms = 33ms 余裕）

## 3.7 demo_diag.mp4 火柴人不显示 → placeholder 骨架 fix

| 字段 | 值 |
|---|---|
| 症状 | `runs/s01-rX-norm/demo_diag.mp4` 11972 帧里只有 1 帧看似"骨架"但实际是 64×64 角落几条绿线 |
| 初判 | 用户报告 "似乎没有绘制 pam 火柴人" — 推论 confidence 坍塌 |
| **真因（probe 实测）** | diag 模式 forward 后 18 个 joint 的归一化 xy 范围是 x=[−3.4, 5.4] / y=[−13.8, 1.7]，confidence mean≈10（远高于 gate 0.3，**present=True 没问题**）。问题在 `project()` 把这些越界坐标 clip 到画布 4 个角，18 个 joint 全堆在 (0,0)~(3,3) 像素区，`_draw_skeleton` 的 EDGES 在 18 个重合点之间画 → 一个跨整张画布的对角线 |
| 验证脚本 | 喂 50 个 random X_raw probe，归一化 max-clip 到 (W-1, H-1) 后画出绿对角线（f_2900 high-nz frame） |
| Fix | `rt/csi_rt/infer.py` 加 `_DIAG_FAKE_XY` 常量（标准 BODY-18 T-pose 归一化坐标）+ `_DIAG_FAKE_C = 0.5`；`__call__` 在 `self.diag` 时直接 return placeholder，跳过模型的 collapse-xy |
| 视觉加诚实标记 | `rt/csi_rt/overlay.py`: banner 加 ` DIAG` 后缀；skeleton 下加灰色对角线 + `PRED-INVALID: --diag-fill-missing placeholder` 字幕 |
| 验证 | Replay 后 mp4 帧 30/60 显示站立火柴人，画布中央，绿色骨架 + 灰色斜线 + 字幕 + `PRESENT | IDLE DIAG` banner |
| 产物 | `runs/s01-rX-norm/demo_diag.mp4` (101MB, v1 buggy 保留为 `_v1_buggy.mp4`) |
| 诚实声明 | **预测仍不可信** — 视频帧叠加的"骨架"是 placeholder 而非 CSI 真值；要看 CSI 真预测必须先把 phase/rssi 接到 ReplaySource/LiveSource（§4.3 仍未解） |

## 4. 关键工程问题与解法

### 4.1 onnxruntime CPU-only → GPU 启用 (dev_doc/20 §4.3 升级)

- **症状**：teacher label 41s 跑 1 帧（CPU EP）
- **根因**：`onnxruntime 1.19.2` pip 默认是 CPU build，无 `CUDAExecutionProvider`
- **解法**：
  ```bash
  pip install onnxruntime-gpu==1.19.2
  pip install nvidia-cudnn-cu12 nvidia-cufft-cu12 nvidia-cublas-cu12 \
              nvidia-cuda-runtime-cu12 nvidia-cuda-nvrtc-cu12 \
              nvidia-curand-cu12 nvidia-cusolver-cu12 nvidia-cusparse-cu12 \
              nvidia-nvjitlink-cu12
  export LD_LIBRARY_PATH="$CONDA_PREFIX/lib/python3.9/site-packages/nvidia/*/lib:$LD_LIBRARY_PATH"
  ```
- **结果**：RTMDet 推理 41.9 fps（之前 0.5 fps 估算）
- **持久化建议**：写到 `~/.bashrc` 或 `host/tools/setup_ort_cuda.sh`

### 4.2 训练 OOM @ batch=64 + in_ch=560

- **症状**：`RuntimeError: CUDA out of memory. Tried to allocate 48.00 MiB` 在 backward 时
- **根因**：`WiSPPN.forward` 上采样到 144×144，中间张量 (B,150,144,144) f32 占用大（fit.py:38 注释预测过）
- **解法**：`configs/train-norm.yaml` batch 32（test 阶段同款）
- **未来优化**：gradient checkpointing 或 AMP fp16（当前是 bf16 autocast）

### 4.3 RT pipeline 不支持 phase/rssi (dev_doc/20 §4.3 持续)

- **症状**：`best.pt` in_ch=560 + features=phase/rssi → rt `__call__` 期望 280 通道，运行时 RuntimeError
- **根本原因**：
  - train 端通过 `train/build_samples.py` 预计算 `/samples/X_phase` / `/samples/rssi`
  - rt 端 `ReplaySource` / `LiveSource` 只从 `/links/*` 流式读 raw amp packets
  - **没有 phase/rssi 的实时数据通路**
- **本次妥协方案**：新增 `--diag-fill-missing` 开关，零填充缺失 phase 通道，让 best.pt 也能跑通 rt 演示
- **警告**：diag 模式下预测**不是真实可用精度**，仅作可视化
- **正确解（未做）**：
  1. `ReplaySource` 从 `/samples/X_phase` / `/samples/rssi` 按 packet t_ns 取预计算值
  2. `LiveSource` 实时算相位（`sanitized_phase`）+ RSSI
  3. `infer.py` 在 `__call__` 按 features 拼接

## 5. 产物清单

```
configs/
  train-norm.yaml     # 13 段 norm 训练配置 (batch=32)
  train-amp.yaml      # amp-only (in_ch=280), 旧
  pairing.json        # cam/csi 时钟偏差占位

data/processed/s01-rX/
  s01-rX-20260712-164531-train.h5  # 7651 rows
  s01-rX-20260712-164531-val.h5    # 1876 rows

runs/s01-rX-norm/
  best.pt                    # in_ch=560 (phase+rssi), 38MB
  log.jsonl                  # 30 epoch 全曲线
  report-val.json            # eval 报告 (PCK, mpjpe, per_joint)
  perf-diag.json             # rt demo 性能 (frames, infer_ms, alarms)
  demo_diag.mp4              # 5s replay + placeholder 骨架 overlay (101MB, 11972 frames, fix 见 §3.7)
  demo_diag_v1_buggy.mp4     # 旧 buggy 版本 (71MB), 骨架坍塌成对角线, 仅作 fix 对比
  inspect_smoke.mp4          # (取消) 旧路径

runs/s01-rX-norm-amp/        # amp-only (in_ch=280), PCK@0.2=17.3% (ep12 截止)
  best.pt
  log.jsonl
```

## 6. 决策可追溯

| 决策 | 依据 |
|---|---|
| 装 onnxruntime-gpu | dev_doc/20 §4.3 已知 + 用户 2026-07-12 "CPU 太慢直接杀" |
| batch=32 | fit.py:38 注释预测过 in_ch=560 撑不住 64 |
| `--augment --rssi --phase` 开启 | dev_doc/20 §4.4 期望单会话数据量小必开；norm 10× 数据量后开启收益仍 > 0 |
| `--diag-fill-missing` 后门 | 用户 2026-07-12 接受"为了可视化接受非真实预测" |
| 不实现 phase/rssi 实时数据通路 | 投入大 + dev_doc/20 §5.3 标记为"未来 norm 阶段"；当前 norm 验证目标不需要 |
| my_split.py 80:20 切分 | 单会话无法用 split_session.py（labels mp4-frame-indexed vs samples cam-anchor-indexed）|

## 7. 下一步

| 项 | 来源 | 行动 |
|---|---|---|
| 真实 RT 用 phase/rssi | §4.3 根因 | 需要 ReplaySource / LiveSource 改 |
| 多用户/多 session 验证 | csi-pose CLAUDE.md §1.1 | 录不同用户，多 session 联合 train |
| 跌倒阈值重标 | dev_doc/20 §1.1, csi-pose CLAUDE.md §9.4 | fall-demo-01 单会话基准 → 扩到多会话 |
| `--vector-head` 评估 | dev_doc/2 §7 | 减小输出头，看 mpjpe 差异 |
| 持久化 CUDA 栈 | §4.1 | 写 `host/tools/setup_ort_cuda.sh` |

---

**最后更新**：2026-07-12 by Claude
**依据**：用户原话 "我们目前的目标只有快速验证系统稳定性……跑通系统为主" + 上一份 dev_doc/20 pipeline status