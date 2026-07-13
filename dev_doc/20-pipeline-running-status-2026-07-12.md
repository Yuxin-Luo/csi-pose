# 20-pipeline-running-status-2026-07-12

> **状态**：全链路 **录 → 训 → 推** 已跑通。模型泛化指标差属预期（见 §4 数据量根因），不影响 pipeline 验证目标。
> **用户决策**（2026-07-12）："test 先把全流程跑通，确认能训练、能实时反馈结果即可，即使效果不好那也是跑通了，我们接下来再加数据量跑 norm 即可"

---

## 1. 目标

验证 **录制 → 切分 → 训练 → 评估 → 实时推理** 全流程在 RTX 4060 上端到端可执行。
效果指标（PCK@0.2、跌倒召回）不在本次验证范围内——已通过单会话（30s 站 + 30s 蹲）确认管道本身无功能性障碍。

## 2. 数据

| 字段 | 值 |
|---|---|
| 会话 | `s01-r1`（test 模式 60s：1:stand:30 + 2:squat:30）|
| H5 | `data/test/s01-r1-20260712-161316.h5` (10MB) |
| MP4 | `data/test/s01-r1-20260712-161334.mp4` (3.7MB, 1026 frames @ 21.3fps) |
| CSI frames | 64502 packets（9 个 link 累加）|
| Cam anchors | 1009 rows / 100% presence / 100% label_ok |
| Split | my_split.py (train 757 / val 150 / gap 102，按 t_ns 80:20 chronologically) |

## 3. Pipeline 执行日志

### 3.1 train.fit (Step 3b)

| run name | features | epochs | in_ch | best PCK@0.2 | best PCK@0.5 | coord loss 末值 |
|---|---|---|---|---|---|---|
| `s01-r1-test` | amp+phase+rssi | 30 | 560 | **1.98%** | 14.5% | 0.00614 |
| `s01-r1-amp` | amp | 10 | 280 | 0% | 0% | 0.0237 |

- 训练 loss 下降正常（coord 0.082 → 0.006 = 14×）说明 fit 链路无 bug
- 评估指标差 → 数据量不足（详见 §4），不是代码问题

### 3.2 train.eval (Step 4)

```
runs/s01-r1-test/report-val.json
{
  "pck": {"0.2": 0.0198, "0.5": 0.1450},
  "mpjpe_px": 157.53,
  "mpjpe_norm": 0.328,
  "kappa": 0.514,
  "n_eval": 59,
  "per_type": {"example-v20": {"0.2": 0.0198, "0.5": 0.1450}}
}
```

### 3.3 rt/demo.py replay (Step 5)

```
runs/s01-r1-amp/perf-rt.json
{
  "mode": "replay",
  "random_weights": false,
  "frames_in": 64502,
  "windows": 1546,
  "valid_windows": 604,
  "dropped_windows": 942,
  "fps_mean": 123.3,
  "infer_ms_p50": 14.7,
  "infer_ms_p95": 15.2,
  "alarms": 0,
  "catchup_windows": 0
}
```

✅ 关键指标：
- **infer p50 = 14.7ms / p95 = 15.2ms** （RTX 4060 单次 WiSPPN forward，50ms tick 预算内）
- 1546 个 50ms window，604 个有效（其余因 packets 不够被丢弃）
- alarms = 0 预期（squat 不触发 IMPACT FSM 的几何条件）

## 4. 已知问题与根因（待 norm 数据量解决）

### 4.1 单会话样本不足 → 泛化失败

- **症状**：train coord loss 正常下降（14×），val PCK@0.2 = 1.98%（@0.5 也仅 14.5%）
- **根因**：train=757, val=150 来自单次 30s 站 + 30s 蹲的会话，**N 太小**且只覆盖 2 类姿态，18 关节里 12 个 joint PCK@0.2 = 0%
- **用户预期**：跑 norm（13 段 580s → ~5000+ 样本）后会改善

### 4.2 cam 物理帧率上限 ≈ 20.85 fps

dev_doc/12 已记录，与训练无关，跳过。

### 4.3 RT 链路不支持 phase/rssi features（已修）

**症状**：用 `s01-r1-test` (in_ch=560, features=['phase','rssi']) 跑 rt demo 报：
```
RuntimeError: Given groups=1, weight of size [150, 560, 3, 3],
expected input[1, 280, 144, 144] to have 560 channels, but got 280 channels instead
```

**根因**：train 端通过 `train/build_samples.py` 预计算 `/samples/X_phase`、`/samples/rssi` 后存进 H5；但 rt 端 `ReplaySource`/`LiveSource` 只从 `/links/*` 流式读取原始 amp packets，没有 phase/rssi 数据通路。

**修复**：[rt/csi_rt/infer.py](../rt/csi_rt/infer.py) 在 `__init__` 读 `ck["config"]["features"]`，若包含 phase/rssi 立即 fail-loud 并提示 retrain amp-only。

**正确方案**（未来 norm 时实现）：
1. `ReplaySource` 从 `/samples/X_phase`/`/samples/rssi` 取预计算特征，按 packet t_ns 对齐
2. 或 `LiveSource` 在 MQTT 链路实时算相位 + RSSI
3. 然后 `infer.py` 在 `__call__` 按 features 拼接对应通道

### 4.4 training 必须用 `--augment`（未启用）

dev_doc/2 §7 推荐 757 样本场景开 `--augment`（4× 数据增强）。本次未启用，原因是单会话样本数对 norm 才有意义；test 模式目标只是确认管道通。

### 4.5 `configs/pairing.json` 不存在（已修）

`rt/demo.py` 默认从 `configs/pairing.json` 读 `cam_correction_ms` / `csi_correction_ms`。仓库没附 example，需手动创建（本次写入了 `{0.0, 0.0}` 占位文件）。

### 4.6 已知未变：CSI 静止检测 disabled、`fall-demo-01` 阈值未跨会话验证

继承自上游 csi-pose CLAUDE.md §1.1 / §9.4，与本次验证无关。

## 5. 待 norm 阶段处理

| 项 | 来源 | 行动 |
|---|---|---|
| 数据量 → norm (13 段 580s) | 用户 2026-07-12 决策 | 跑 `./host/boot_recording.sh norm s01-rX` |
| `--augment` | dev_doc/2 §7 | norm 训练时启用 |
| in_ch=560 的 phase/rssi rt 通路 | §4.3 | norm 阶段解决 |
| `--vector-head` 头替换 | dev_doc/2 §7 | norm 训练时评估 |

## 6. 产物清单

```
configs/
  train.yaml             # phase+rssi 训练配置
  train-amp.yaml         # amp-only 训练配置（rt 友好）
  pairing.json           # cam/csi 时钟偏差占位（dev_doc/20 §4.5）
data/processed/s01-r1/
  s01-r1-20260712-161316-train.h5  # 757 rows
  s01-r1-20260712-161316-val.h5    # 150 rows
runs/
  s01-r1-test/
    best.pt              # in_ch=560 (phase+rssi)，训练指标
    log.jsonl            # 30 epoch 全曲线
    report-val.json      # eval 报告
  s01-r1-amp/
    best.pt              # in_ch=280 (amp-only)，rt 友好
    log.jsonl
    perf-rt.json         # rt replay 性能
```

## 7. 决策可追溯

| 决策 | 依据 |
|---|---|
| 用 my_split.py 而非 split_session.py | `/labels/*` 是 mp4-frame-indexed (1026) 与 `/samples/*` cam-anchor-indexed (1009) 不 row-aligned |
| `train-amp.yaml` 用 10 epochs | test 目标只需确认 in_ch=280 路径通 |
| retrain amp 而不是改 infer.py 拼接 phase | rt pipeline 缺数据通路（§4.3），修 infer 是 fail-loud 防御，**正确解是补 rt 数据流**——本次留给 norm |
| rt smoke 用 `--fast --headless --duration 5` | `--fast` 关闭 phase alignment，headless 不弹窗 |

---

**最后更新**：2026-07-12 by Claude
**依据**：用户原话 "test 先把全流程跑通……即使效果不好那也是跑通了"