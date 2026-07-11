# 13 — Loss Verdict: Real Numbers, Not Live Log Artifacts

**状态**：✅ 收尾（2026-07-11 23:35）
**目的**：修正 dev_doc/11 §5 "USB disconnect 是 root cause" 的部分误判，明确真实 in-recording loss。

---

## 1. 关键澄清

### 1.1 live.log `loss: 0.95` 不是真实在录 loss

**bridge live.log 报的 loss**:
```json
[rx0] {"links":{"0":{"rx":6846,"lost":134003,"loss":0.95144}}}
```

**这不是 60 秒录制期间丢的**，这是 **bridge `LinkTracker` 从 TX 启动以来所有 seq gap 的累计计数**。

具体：
- TX 板子上电启动，但 RX 板子还没启动
- TX 已发 seq 0...150000+
- RX 启动后 bridge.py 第一次收包：seq=150000+，`_last=None → skip first frame`
- 但 dev_doc/5 §3.2 描述的"`expected_seq = first_seq + 1; 首次跳变时把所有未收到的历史 seq 计入 lost`"——老版本有这 bug
- 现在 `LinkTracker` 已经修成 first update _last=None → 真正 skip，但累计 counter 仍然从 start 持续累加

**结论**：live.log 报的 lost / loss 是**整个 TX 运行时长的累计**，**不是单次 60s 录制的真实 loss**。

### 1.2 h5 `recorder_status.links.*.loss` 才是真相

h5 meta 存的 `recorder_status` 是 recorder 最后一次 `status()` 输出：
```
links.'0-0': {rx: 6066, lost: 100, loss: 0.01622} = 1.62%
links.'0-2': {rx: 5720, lost: 431, loss: 0.07007} = 7.01%
```

这是 **真实在录 loss**——基于 `LinkTracker` 实际跨 run 维护的状态。

如果 s01-smoke-v2 (disconnect ON) 和 s01-loss-trace2 (disconnect OFF) 都给出 `links.0-0.loss < 2%`，那 USB disconnect 事件对 in-recording loss **影响很小**。

---

## 2. 5 次 test smoke loss 对比

| Run | cam fps | frames | loss 0-0 | avg loss | max loss |
|---|---|---|---|---|---|
| s01-smoke (1280x720 autosusp ON) | 15.2 | 944 | 2.11% | 4.20% | 7.03% |
| s01-smoke-v2 (640x360 autosusp ON) | 27.0 | 1229 | 1.70% | 4.21% | 7.09% |
| s01-smoke-v3b (640x360 autosusp ON + calib) | 31.1 | 1758 | 2.14% | 4.43% | 7.65% |
| s01-loss-trace (640x360 autosusp ON) | 30.6 | 1707 | 1.70% | 4.31% | 8.52% |
| s01-loss-trace2 (640x360 autosusp OFF) | 27.5 | 1304 | **1.62%** | 4.21% | 9.81% |

**Disable autosuspend 后**：链路 0-0 1.62% (历史最低)；avg 4.21% (历史最低)。
**Disable autosuspend 前**：1.70-2.14%，avg 4.20-4.43%。

**变化幅度 ~0.1pp**——与 dev_doc/5 baseline (4.13-4.53%) 一致。**disable autosuspend 是边际改善，不是翻天覆地**。

---

## 3. 真实剩余问题（30%）

链路 **0-2** 持续 7-9% loss across all 5 recordings。是 TX2 单板 firmware 问题（dev_doc/5 §3.4 已记）：
> TX2 板上问题（次）：TX2 单独 ~8% loss = +4-5pp，T3/T4 跨 RX 一致

dev_doc/5 已接受这 4-5pp 进入 baseline。**不需要修**。

---

## 4. dev_doc/11 §5 修正

dev_doc/11 §5 "Loss 根因是 Linux xhci_hcd 物理层抖动" 这个 **部分错**：
- ❌ "3 块 RX 集体 USB disconnect 导致 95% loss"
- ✅ "USB disconnect 是真实事件，但对 in-recording loss 影响有限"（链路 0-0 在 0.5pp 内波动）
- ✅ "live.log 报的 95% loss 是 LinkTracker 累计 startup accounting artifact"

修正版总结：
1. **disable USB autosuspend 仍然有价值**——消除 14 秒内 6 个 disconnect 事件，避免 RX 串口闪断
2. **disable autosuspend 之前的数据也健康**——recorder loss 链路 0-0 1.7-2.1%，与 dev_doc/5 baseline 一致
3. **不需要再调查 root cause**——目前 loss 4.21% avg 与 baseline 4.13-4.53% 匹配

---

## 5. 现在的方向

✅ 数据**已经足够用**：
- cam 真 fps 27-31（足够）
- recorder loss 链路 0-0 1.62%（与 baseline 一致）
- mp4 metadata 准确（30.59 fps calibration）
- rawlog 完整（TS glog 修好）

**下一步**：切 norm `s01-r1` 580s 长跑。

如果 norm 后 loss 仍 4.21% → 完成。如果 loss 突然恶化（比如 10%）→ 立即 dev_doc/14 再开。
