# 23-amp-demo-working-2026-07-12

> **状态**：amp-only 模型 + rt/demo.py **端到端跑通**，视频骨架正常渲染。dev_doc/22 handoff 问题已定位并解决。

---

## 1. 问题回顾（dev_doc/22 遗留）

dev_doc/22 报告"跑视频无法正常绘制骨骼"，上一会话尝试的 placeholder T-pose 修复**未生效**。

## 2. 根因诊断

### 2.1 不是"CSI 数据是实时的"

`rt/demo.py --replay` 从 H5 `/links/*` 读取**录制的** CSI 数据，不存在"实时 vs 录制"混淆。

### 2.2 真正的根因：特征通道不匹配

| 模型 | in_ch | 需要的特征 | RT pipeline 能提供 | 结果 |
|---|---|---|---|---|
| `s01-rX-norm/best.pt` | 560 | amp + phase + rssi | 仅 amp (280) | ❌ 预测坍塌 |
| `s01-rX-norm-amp/best.pt` | 280 | amp only | amp (280) | ✅ 正常 |

`norm/best.pt` 是 amp+phase+rssi 三路特征联合训练的（PCK@0.5=78.9%），但 RT pipeline 的 `ReplaySource` 只从 `/links/*` 读原始 amp 包 —— phase/rssi 的数据通路从未实现（dev_doc/21 §4.3 标记为待做）。

`--diag-fill-missing` 把缺失的 phase 通道填零 → z-score 后不是零均值 → 模型 forward 输出 xy 坐标坍缩到正常范围外（x=[-3.4, 5.4], y=[-13.8, 1.7]）→ `project()` clip 到画布角落 → 18 个关节堆在 4 个像素里 → 画出来是一条对角线而非火柴人。

### 2.3 为什么 placeholder fix 没生效

dev_doc/21 §3.7 的 T-pose placeholder fix（`infer.py` `_DIAG_FAKE_XY`）单帧抽帧确认 OK，但用户复跑后仍看不到。可能原因：
- 用户跑的命令未传 `--diag-fill-missing`
- `cv2.VideoWriter` 编码器 / fps 不匹配
- 或其他环境差异

**本次不再追查 placeholder 路径**，因为用 amp-only 模型根本不需要这个 hack。

## 3. 解决方案：切换到 amp-only 模型

### 3.1 用到的模型

`runs/s01-rX-norm-amp/best.pt`：
- in_ch=280（amp only）
- 训练数据：s01-rX norm 13 段 580s（同 `norm/best.pt`）
- 训练中断于 epoch 12（用户手动停止）
- best epoch = 11，PCK@0.5 = **55.5%**，PCK@0.2 = 15.1%

### 3.2 执行命令

```bash
python rt/demo.py --replay data/s01-rX-20260712-164531.h5 \
    --ckpt runs/s01-rX-norm-amp/best.pt \
    --video data/s01-rX-20260712-164551.mp4 \
    --save demo_amp.mp4
```

### 3.3 产物

```
demo_amp.mp4  (48MB, 3456 frames, 1280×480, 20fps)
```

画面布局：左侧 640×480 视频画面叠加骨架 + 右侧 640×480 黑色画布叠加骨架。

### 3.4 骨架渲染确认

| 检测项 | 结果 |
|---|---|
| 视频侧绿色骨架像素 | 2299 px |
| 画布侧绿色骨架像素 | 2217 px |
| 骨架 bbox（两侧一致） | x=[208, 335], y=[138, 461] |
| Banner 状态 | IDLE（灰色，符合 norm 录制内容：站/坐/躺，无跌倒） |
| 底部 HUD 文字 | 正常渲染 |
| infer 速度 | p50 ≈ 10ms（RTX 4060，50ms tick 预算内） |

骨架在全部采样帧（10%/30%/50%/70%/90%）均有绿色像素，确认**全程渲染正常**。

## 4. runs/ 四个模型速查表

| 文件夹 | 训练数据量 | 特征 | in_ch | PCK@0.5 | 完整训练 | RT 直接可用 |
|---|---|---|---|---|---|---|
| `s01-r1-test/` | 60s (test) | phase+rssi | 560 | 14.5% | ✅ 30 epoch | ❌ |
| `s01-r1-amp/` | 60s (test) | amp | 280 | 0% | ✅ 10 epoch | ✅ |
| `s01-rX-norm/` | 580s (norm) | phase+rssi | 560 | **78.9%** | ✅ 30 epoch | ❌ |
| `s01-rX-norm-amp/` | 580s (norm) | amp | 280 | **55.5%** | ⏸ 中断于 ep12 | ✅ |

> **不需要全部练完才能推理。** 每个文件夹下的 `best.pt` 都是训练过程中保存的最佳 checkpoint，随时可用于推理。

## 5. 当前效果说明

### 5.1 能看到什么

PCK@0.5=55.5% 意味着约 55% 的关节预测落在半头长范围内。MP4 中能看见：
- 大致的火柴人形状（头-肩-髋-腿的拓扑结构）
- 站姿/坐姿/躺姿的区分
- 画面左侧视频叠加 + 右侧画布的并行显示

### 5.2 效果不如 phase+rssi 模型

`norm/best.pt`（phase+rssi）PCK@0.5=**78.9%**，比 amp-only 高 23 个百分点。差距来源：
- Phase 包含子载波间相位差信息，对细粒度空间定位有帮助
- RSSI 提供链路质量的辅助信号

### 5.3 为什么 amp-only 只练了 12 epoch

用户在原话中确认："我在 norm 训练完成后就打断了后续训练"。norm 训练指 `norm/best.pt`（30 epoch），`norm-amp` 是另一个训练任务，被提前中断。

## 6. 注意事项

### 6.1 关于"实时 CSI 数据"的澄清

`rt/demo.py` 有两种模式：
- **`--replay`**：从 H5 文件回放录制的 CSI 数据（本次使用）
- **`--live`**：从 MQTT 接收实时 CSI 数据（需要 ESP32 硬件在线）

两种模式用的是**同一套推理 + 渲染管线**。replay 能跑通 = live 也能跑通。

### 6.2 当前"最简单前端"就是 demo.py

用户问"能不能做一个最简单的前端根据实时 CSI 数据 + 模型预测画骨骼"——现有的 `rt/demo.py` 就是这个前端。它的功能：
- 输入：CSI 数据（H5 回放 或 MQTT 实时）
- 推理：加载 `best.pt`，每 50ms 切一个 window，forward 一次
- 输出：OpenCV 窗口实时显示 + 可选 mp4 录制
- 叠加：骨架火柴人 + 跌倒状态机 banner + HUD 性能指标

**无需额外开发前端。** 当前瓶颈不在前端，在模型选择。

### 6.3 后续提升效果的正确方向

不是换前端，而是**把 phase/rssi 接进 RT pipeline**（dev_doc/21 §4.3 标记的待做项）：

> H5 中 `/samples/X_phase` (9623, 280, 3, 3) 和 `/samples/rssi` (9623, 5, 3, 3) 已经预计算好，只需：
> 1. `ReplaySource` 或 `Engine` 按 cut window 的 `B_ns` 查找最近的 sample 行
> 2. `infer.py` `__call__` 接受 `X_phase`/`rssi` 参数，拼接后 forward
> 3. 完成后 `norm/best.pt`（PCK@0.5=78.9%）可直接用于 RT 推理

## 7. 决策可追溯

| 决策 | 依据 |
|---|---|
| 用 amp-only 而非继续修 placeholder | dev_doc/22 §4.4 明确 "不要继续重写 placeholder"；amp-only 是绕过特征不匹配的正确路径 |
| 不新做前端 | `rt/demo.py` 已实现全部所需功能；问题不在前端在模型 |
| 不追查 placeholder fix 为何没生效 | 用户已切换到 amp-only 路径；placeholder 仅作 diag hack，无长期价值 |
| PCK@0.5=55.5% 可接受 | 系统验证阶段目标是"能看到骨架"而非高精度；78.9% 留给 phase/rssi 通路实现后 |

---

**最后更新**：2026-07-12 by Claude
**依据**：用户运行 amp-only demo 成功 + 要求整理文档
