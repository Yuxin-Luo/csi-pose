---
title: "csi-pose 链路性能测试"
subtitle: "TX/RX 各组合下的丢包率 / pps 分布 / 公平性"
author: "LYX (基于 2026-07-10 实测数据)"
date: "2026-07-10"
institute: "ESP32-S3 × 6 板系统"
documentclass: beamer
classoption: [aspectratio=169, 10pt]
theme: default
colortheme: default
mainfont: "Noto Serif CJK SC"
sansfont: "Noto Sans CJK SC"
CJKmainfont: "Noto Serif CJK SC"
header-includes:
  - \usepackage{ctex}
  - \usepackage{booktabs}
  - \usepackage{graphicx}
  - \setbeamertemplate{footline}{\hfill\insertframenumber/\inserttotalframenumber\quad}
  - \setbeamertemplate{navigation symbols}{}
  - \setbeamercolor{frametitle}{bg=blue!10}
  - \setbeamercovered{transparent}
---

# 测试背景

## 测试目标

- **场景**：4 种 TX/RX 组合 × 多 demo，每 demo 60 秒
- **采集指标**：
  - 帧接收数 (`rx`)、丢包数 (`lost`)、丢包率 (`loss%`)
  - 接收速率 (`pps`)、CRC 错误、重启次数
- **数据来源**：
  - `host/bridge/bridge.py` 的 1s 周期 JSON 输出
  - `logs/rx*-*.rawlog` 离线解析（chunked streaming + 0xC51D magic 切帧）
- **真实 loss 计算**：用 `Σ(Δlost) / Σ(Δrx)` 排除 bridge `lost` startup accounting 伪影

# 测试矩阵

## 4 种场景

| ID | 场景 | TX 数 | RX 数 | 期望总 pps | 备注 |
|---|---|---|---|---|---|
| T1 | 1TX→1RX | 1 | 1 (RX0) | ~100 | 物理 baseline |
| T2 | 1TX→3RX | 1 | 3 (RX0/1/2) | ~300 (按链路) | 测 USB host 争抢 |
| T3 | 3TX→1RX | 3 | 1 (RX0) | ~300 (按链路) | 测 3TX 碰撞 |
| T4 | 3TX→3RX | 3 | 3 (RX0/1/2) | ~900 (按链路) | 满载场景 |

每场景 2-3 个 demo，TX/RX 持续运行（不重启），仅重置 bridge。

# 测试方法

## 工具链

- **bridge**：`host/bridge/bridge.py --no-mqtt --status-period 1.0` 每秒打印 JSON
- **超时控制**：`timeout 65` 强制 SIGTERM，3 桥并行（`&` + `wait`）
- **rawlog**：binary chunks 按 0xC51D magic 切 130B CSI 帧
- **CSI 帧结构**（[csi_link/wire.h:28-41](../../firmware/components/csi_link/include/csi_link/wire.h)）：
  - 2B magic (0xC51D) + 1B rx_id + 1B tx_idx + 4B seq + 4B esp_timer_us + ...
  - tx_idx 在 offset 3，用于按 TX 分桶

## bridge `lost` startup accounting 伪影

| 场景 | bridge lost 起步 | 报告 loss% | 真实 loss% |
|---|---|---|---|
| T1 demo1 (TX 刚启动) | 0 | 0.15% | 0.15% |
| T1 demo2 (TX 已运行 3min) | 25,042 | 77.05% | 0.06% |
| T2 demo1 (TX 已运行 7min) | 41,351 | 84.70% | 0.21% |

**伪影机制**：bridge 启动时收到第 1 帧，把所有"启动前已广播但未听"的包计入 `lost`。  
**修法**：所有 analysis.md 用 `Σ(Δlost) / Σ(Δrx)` 计算真实 loss。

# T1: 1TX → 1RX (物理 baseline)

## T1 demo1 + demo2

| demo | 总 rx | 真实 loss% | 丢包事件 | pps 均值 | 期望 pps |
|---|---|---|---|---|---|
| T1 demo1 | 6,833 | **0.146%** | 10 | 105.21 | 100 |
| T1 demo2 | 6,297 (稳定期) | **0.064%** | 4 | 101.47 | 100 |

**共同特征**：
- CRC 错误 = 0
- Reboot = 0
- loss < 0.15%（远低于 §2.5 的 5% 阈值）
- pps 在 98-105 范围（100Hz ±15% jitter + USB CDC buffer flush）

# T2: 1TX → 3RX (3 RX 并行)

## T2 demo1 + demo2

| demo | RX0 loss% | RX1 loss% | RX2 loss% | avg loss% | pps/RX |
|---|---|---|---|---|---|
| T2 demo1 | 0.159% | 0.286% | 0.194% | **0.213%** | 101.5 |
| T2 demo2 | 0.307% | 0.239% | **0.638%** | **0.394%** | 101.4 |

**客观观察**：
- 总 pps = 3 × ~101 = ~305（与 3 RX 独立接收一致）
- 综合 loss 0.21-0.39%（比 T1 的 0.06% 高 3-6×）
- RX2 在 demo2 显著升高到 0.638%（用户已确认：RX2 被人为 LOS 阻挡）
- CRC 错误 = 0
- 跨 RX 极差 0.13-0.40pp

# T3: 3TX → 1RX (3TX 碰撞)

## T3 demo1 + demo2 (TX 修复后)

| demo | TX0 loss% | TX1 loss% | TX2 loss% | 总 loss% | 总 pps |
|---|---|---|---|---|---|
| T3 demo1 | 2.59% | **1.13%** | **9.52%** | 4.29% | 294.48 |
| T3 demo2 | 3.09% | **0.84%** | **7.43%** | 3.72% | 296.07 |

**客观观察**：
- 总 pps = ~295（与 3 TX × 100Hz 目标一致）
- 综合 loss 3.72-4.29%（比 T1 0.06% 高 60-70×）
- **TX2 在两个 demo 都最差**（9.52% / 7.43%）
- TX1 在两个 demo 都最佳（1.13% / 0.84%）
- TX0/TX2 极差 8.4pp（单 demo 内最大极差）
- CRC 错误 = 0
- TX1 ≈ 单 TX baseline；TX0/TX2 显著高于 baseline

# T4: 3TX → 3RX (满载场景)

## T4 三个 demo 跨 RX 矩阵

| demo | RX0 视角 (TX0/1/2) | RX1 视角 (TX0/1/2) | RX2 视角 (TX0/1/2) |
|---|---|---|---|
| T4 demo1 | 3.12 / 0.66 / 7.82 | 1.28 / 3.63 / 9.82 | 3.18 / 3.89 / 7.40 |
| T4 demo2 | 2.79 / 1.21 / 7.02 | 2.86 / 2.37 / 6.89 | 2.98 / 3.08 / 6.66 |
| T4 demo3 | 2.79 / 0.95 / 7.05 | 2.70 / 2.94 / 7.74 | 3.09 / 3.79 / 6.09 |

| demo | 综合 loss% | 总 pps | CRC |
|---|---|---|---|
| T4 demo1 | **4.53%** | 295 | 0 |
| T4 demo2 | **3.99%** | 297 | 0 |
| T4 demo3 | **4.13%** | 294 | 0 |

# 跨场景对比

## 8 demo 真实 loss 总览

| 场景 | demo | 总 pps | 综合 loss | 单链路 loss 极差 | 备注 |
|---|---|---|---|---|---|
| T1 (1TX→1RX) | d1 | 105 | **0.146%** | — | baseline |
| T1 | d2 | 101 | **0.064%** | — | baseline |
| T2 (1TX→3RX) | d1 | 305 | **0.213%** | 0.13pp | 3 RX |
| T2 | d2 | 305 | **0.394%** | 0.40pp | RX2 LOS |
| T3 (3TX→1RX) | d1 | 294 | **4.29%** | 8.39pp | TX2 弱 |
| T3 | d2 | 296 | **3.72%** | 6.59pp | TX2 弱 |
| T4 (3TX→3RX) | d1 | 295 | **4.53%** | 8.54pp | TX2 跨 RX |
| T4 | d2 | 297 | **3.99%** | 5.15pp | TX2 跨 RX |
| T4 | d3 | 294 | **4.13%** | 4.79pp | TX2 跨 RX |

# 主效应分解

## loss 贡献按场景

| 效应 | 单 RX/RX 视角增量 | 证据 |
|---|---|---|
| **3TX 碰撞** | 0.06% → 4.0% = **+3.94pp** | T1 → T3 跨场景对比 |
| **TX2 板上问题** | TX2 单独 ~7-8% loss | 11 个 RX×TX 测点稳定 |
| **3 RX USB 争抢** | 0.06% → 0.21-0.39% = +0.15-0.33pp | T1 → T2 跨场景对比 |
| **物理位置** | 跨 RX 极差 0.5pp | T4 demo1/2/3 跨 RX |

# TX2 板上问题

## 跨 5 demo × 11 测点统计

| 测点来源 | TX2 loss% |
|---|---|
| T3 demo1 (RX0) | 9.52% |
| T3 demo2 (RX0) | 7.43% |
| T4 demo1 (RX0) | 7.82% |
| T4 demo1 (RX1) | 9.82% |
| T4 demo1 (RX2) | 7.40% |
| T4 demo2 (RX0) | 7.02% |
| T4 demo2 (RX1) | 6.89% |
| T4 demo2 (RX2) | 6.66% |
| T4 demo3 (RX0) | 7.05% |
| T4 demo3 (RX1) | 7.74% |
| T4 demo3 (RX2) | 6.09% |

| 范围 | 均值 | stdev |
|---|---|---|
| 6.09% – 9.82% | **7.59%** | **1.10pp** |

**对比**：TX0/TX1 跨同样测点的均值 = 2.5-2.8%（约 TX2 的 1/3）

# §2.5 最终验收

| 指标 | §2.5 目标 | T4 demo1 | T4 demo2 | T4 demo3 | 判定 |
|---|---|---|---|---|---|
| 总 pps | 290-310 | 295 | 297 | 294 | 通过 |
| 综合 loss | < 5% | 4.53% | 3.99% | 4.13% | 通过 |
| CRC 错误 | 0 | 0 | 0 | 0 | 通过 |
| Reboot | 0 | 0 | 0 | 0 | 通过 |
| 单 TX 视角 | (无) | TX2 8.35% | TX2 6.86% | TX2 6.96% | **TX2 不达标** |

# rawlog vs bridge 对账

## 8 demo 总差异

| 场景 | bridge rx 总和 | rawlog CSI 总和 | 差异 | 差异率 |
|---|---|---|---|---|
| T1 d1 | 6,833 | 6,804 | -29 | 0.42% |
| T1 d2 | 7,462 | 7,406 | -56 | 0.75% |
| T2 d1 (3 RX) | 22,300 | 22,250 | -50 | 0.22% |
| T2 d2 (3 RX) | 22,271 | 22,189 | -82 | 0.37% |
| T3 d1 | 7,360 | 7,300 | -60 | 0.82% |
| T3 d2 | 7,360 | 7,300 | -60 | 0.82% |
| T4 d1 (3 RX) | 58,049 | 58,010 | -39 | 0.07% |
| T4 d2 (3 RX) | 58,383 | 58,204 | -179 | 0.31% |
| T4 d3 (3 RX) | 57,868 | 58,195 | +327 | 0.56% |

# 开放问题

## 未在本次测试范围内

1. **TX2 板上问题的根因**（11 测点稳定 7.59% ± 1.10pp）
   - 候选：晶振个体差异 / esp_timer 漂移 / 天线方向
   - 复现方法：TX0 ↔ TX2 位置互换，重跑 T4
2. **5 分钟长跑 T4 demo**：本次每 demo 仅 60s，长跑下 loss 趋势未知
3. **bridge.py `lost` startup 逻辑**：是否在 csi-pose 上游提 PR 加 `startup_lost` 字段
4. **TX0/TX1 互有胜负的物理位置因素**：T4 三 demo 跨 RX 排序不一致

# 数据落盘位置

## 所有源文件

| 路径 | 内容 |
|---|---|
| [data/1-single-tx-single-rx/demo1-2/](data/1-single-tx-single-rx/) | T1 2 demo 的 rawlog + analysis.md + rawlog_summary.json |
| [data/2-single-tx-three-rx/demo1-2/](data/2-single-tx-three-rx/) | T2 2 demo × 3 RX |
| [data/3-three-tx-single-rx/demo1-2/](data/3-three-tx-single-rx/) | T3 2 demo |
| [data/4-three-tx-three-rx/demo1-3/](data/4-three-tx-three-rx/) | T4 3 demo × 3 RX |
| [dev_doc/5-loss-throughput-baseline-2026-07-10.md](dev_doc/5-loss-throughput-baseline-2026-07-10.md) | 9 demo 汇总 dev_doc |

# 致谢 / 数据溯源

- 硬件：3 块 ESP32-S3 TX + 3 块 ESP32-S3 RX
- 固件：[firmware/direct_download/](../../firmware/direct_download/)（与 upstream tx 等价）
- Bridge：[host/bridge/bridge.py](../../host/bridge/bridge.py)
- 协议定义：[firmware/components/csi_link/include/csi_link/wire.h](../../firmware/components/csi_link/include/csi_link/wire.h)
- 测试日期：2026-07-10 13:45 - 14:50
- 测试人：LYX（基于用户指令执行）
