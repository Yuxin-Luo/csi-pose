# CLAUDE.md — csi-pose 行为规范

> 本目录是开源参考项目 **csi-pose**（基于 ESP32-S3 的 WiFi CSI 2D 人体姿态估计 + 跌倒检测）。
> 本文件定义 Claude 在本目录下工作的行为边界。
> 用户拥有最终决定权，Claude 不得违背用户明示要求。

---

## 0. 第一性原理（最重要）

**从原始需求和问题本质出发，不从惯例或模板出发。**

做任何决策前，必须能回答"为什么"。如果一个动作、推荐、参数没有清晰的"为什么"，立刻停下澄清，而不是用套话敷衍。

csi-pose 的"为什么"清单（每次改代码前先对一遍）：

- 为什么要 **link matrix** 而非 **antenna matrix**？因为 6 块 ESP32-S3 是物理分离的独立 NIC
- 为什么要用**下包络**做时钟同步？因为 USB 串口延迟只能让包迟到、不能早到，下凸包是无偏估计
- 为什么要**规则式 FSM** 而非端到端分类？因为数据规模（单次会话 11 次跌倒）撑不起深度分类器
- 为什么跌倒阈值**不能直接套**？因为所有数字都来自 `fall-demo-01` 单次会话

---

## 1. 项目定位与诚实声明

| 字段 | 内容 |
|---|---|
| **项目名** | csi-pose |
| **一句话** | 用 WiFi CSI 估计 2D 人体姿态（18 关节），并基于姿态做跌倒检测 |
| **核心创新** | 把 WiSPPN（Intel 5300 单 NIC，3×3 **antenna matrix**）移植到 6 块 ESP32-S3（3TX×3RX）的 3×3 **link matrix** |
| **训练范式** | webcam+RTMPose 当教师打伪标签，CSI 学 PAM 回归；**推断时无需摄像头** |
| **输入** | 6 块 ESP32-S3 通过 ESP-NOW 周期性 beacon（~103 pps） + 3 块 RX 串口回传 130B CSI 帧 |
| **输出** | 18 关节 2D 坐标 + 站/躺姿态 + IDLE→IMPACT→ALARM 跌倒状态机 |

### 1.1 Honest scope（必须承认的局限）

- 跌倒阈值由**单次会话** `fall-demo-01` 标定（11 次脚本化跌倒，10/11 召回，2 FP）
- **CSI 静止检测当前已禁用**（站/躺的运动能量分布重叠）
- 跌倒确认靠姿态几何，不靠 CSI 能量
- **这不是验证过的医疗或安全设备** — README 已明确声明

任何对阈值的调整都必须先回到 `fall-demo-01` 复现，再考虑外推。

---

## 2. 5 个子系统

```
① firmware/  3 ESP32-S3 TX 广播 ESP-NOW (~103 pps)
                3 ESP32-S3 RX 提取 CSI → 130B 帧过串口
                csi_link/  是共享 TX/RX 组件
② host/      bridge:    串口→原始日志保留 + MQTT 中继
              capture/   串口抓包
              recorder/  HDF5 会话写盘
              csi_pipe/  时钟拟合 / 对齐 / 采样库（核心）
              tools/     运维 CLI
③ teacher/   同步录制的 webcam 视频 → RTMDet → RTMPose 生成姿态标签
④ train/     训练 CSI→PAM 回归网络（WiSPPN-ESP）
⑤ rt/        ~20Hz 实时姿态估计 + 跌倒检测 demo
```

数据流：**CSI 包 → bridge → HDF5 → teacher 打标签 → train → rt 推理 → demo**

---

## 3. 关键设计决策（决策可追溯）

| 决策 | 依据 |
|---|---|
| **下包络时钟同步** | USB 串口延迟只能让包迟到不能早到 → `(board_time, host_time)` 散点的**下凸包**是无偏估计 |
| **link matrix 而非 antenna matrix** | 6 块 ESP32-S3 物理上分离，每块是独立 NIC，3TX×3RX 等价于 3×3 信道矩阵 |
| **规则式跌倒 FSM** | 数据规模（单次会话 11 次跌倒）撑不起深度分类器；规则可解释、可调试 |
| **至少 2/3 cues 触发 IMPACT** | R1 骨盆快速下降 + R2 站→躺 + R3 头部掉到下半屏；OR-of-cues 太敏感、AND 漏报 |
| **ALARM 需 hold 窗口** | 避免"蹲下""靠墙"等动作被误报为跌倒 |
| **CSI 静止检测暂关** | 站/躺姿态的运动能量分布重叠；判别靠姿态几何而非 CSI 能量 |
| **PAM（Pose Appearance Map）回归** | 输入 50ms 窗口的 CSI 振幅张量，输出 18 关节 2D 坐标；端到端可微 |

---

## 4. 目录结构（csi-pose 实际布局）

```
csi-pose/
├── README.md / README.ko.md          ← 总览（EN/KR）
├── CLAUDE.md                          ← 本文件
├── LICENSE                            ← 上游版权，禁动
├── requirements.txt
│
├── firmware/                          ← ESP32-S3 固件
│   ├── tx/                            ← 3 块 TX 板
│   ├── rx/                            ← 3 块 RX 板
│   └── components/csi_link/           ← 共享 TX/RX 组件
│
├── host/                              ← 上位机原生部分（Windows 侧）
│   ├── bridge/                        ← 串口→日志 + MQTT
│   ├── capture/                       ← 抓包
│   ├── csi_host/                      ← 主机端 CSI 工具
│   ├── csi_pipe/                      ← 时钟拟合/对齐/采样 ⭐
│   ├── recorder/                      ← HDF5 会话写盘
│   └── tools/                         ← 运维 CLI
│
├── teacher/                           ← 伪标签生成
│   ├── csi_teacher/                   ← 库
│   └── teacher.py                     ← 入口
│
├── train/                             ← 训练
│   ├── csi_train/                     ← 训练库
│   ├── train.py                       ← 主训练脚本
│   ├── empty_session.py
│   ├── probe.py
│   ├── run_ablation.py
│   ├── run_m25.py
│   └── split_session.py
│
├── rt/                                ← 实时推理
│   ├── csi_rt/                        ← 实时库（含 FSM 推断）
│   └── demo.py                        ← demo 入口
│
├── configs/                           ← YAML 配置
│   ├── boards.example.yaml            ← 板卡拓扑
│   ├── train.example.yaml             ← 训练超参
│   ├── rt.yaml                        ← 实时配置
│   └── rt-live-relay.yaml
│
├── figures/                           ← README 配图
└── docs/figures/                      ← 文档配图
```

---

## 5. 关键文件速查

| 想找什么 | 看哪里 |
|---|---|
| 系统总览 / 原理图 | `README.md`、`README.ko.md` |
| 板卡拓扑（MAC、串口、角色） | `configs/boards.example.yaml` |
| 训练超参 | `configs/train.example.yaml` |
| 实时配置 / MQTT 中继 | `configs/rt.yaml`、`configs/rt-live-relay.yaml` |
| TX/RX 共享组件（CSI 帧格式） | `firmware/components/csi_link/` |
| 时钟拟合 / 对齐 / 采样 | `host/csi_pipe/` |
| HDF5 会话格式 | `host/recorder/` |
| 教师打标签入口 | `teacher/teacher.py` |
| 训练入口 | `train/train.py` |
| 实时 demo 入口 | `rt/demo.py` |
| 跌倒 FSM 实现 | `rt/csi_rt/`（具体文件待 `rt/` 内核对） |

---

## 6. 沟通原则

| 规则 | 说明 |
|---|---|
| **不要假设我清楚自己想要什么** | 动机或目标不清晰时，停下来**主动提问**，不要猜测 |
| **目标清晰但路径不是最短的** | 直接告诉我，并**建议更好的办法** |
| **遇到问题追根因** | **不打补丁**。每个决策都要能回答"为什么" |
| **输出说重点** | **砍掉一切不改变决策的信息**。少废话 |

---

## 7. API 速率限制（硬约束）

| 指标 | 上限 |
|---|---|
| **RPM（Requests Per Minute）** | **< 200** |
| **TPM（Tokens Per Minute）** | **< 10,000,000** |

超出时 Claude 必须主动降速（串行代替并行、合并请求）。

> 注：ESP32 烧录受 USB 串口物理限制（典型 < 5 次/分钟），不必单独设限。

---

## 8. 开发/调研文档规范（强制）

### 8.1 文件命名（必读）

每次进行开发或调研类的任务，**必须**留下过程文档，存入 `dev_doc/`，命名格式：

```
<序号>-<内容>-<日期>.md
```

示例：
- `1-csi-pose-codebase-walkthrough-2026-06-26.md`
- `2-clock-sync-verification-2026-06-26.md`
- `3-fall-fsm-param-tuning-2026-06-26.md`

序号从 1 开始，**XML 参考表**使用 `0-references-<日期>.xml`。

### 8.2 文档内容最低要求

每份 dev_doc 至少包含：
- **调研/开发目标**
- **方法/工具**（用了哪些 API、库、命令）
- **关键发现 / 决策依据**（附可信链接）
- **结论 / 待澄清事项**

### 8.3 XML 参考表（强制）

进行调研过程中必须维护一份 XML 表格（`0-references-<日期>.xml`），每个参考资料登记：

```xml
<ref id="r001">
  <title>...</title>
  <type>repo|paper|doc|dataset|tool|user-code</type>
  <url>...</url>
  <local_path>...</local_path>
  <status>active|archived|contact-required|404</status>
  <trust>high|medium|low</trust>
  <used_in>...（被哪些 dev_doc 引用）</used_in>
  <notes>...</notes>
</ref>
```

目的：便于未来其他 agent 快速查证、复用引用、避免重复调研。

---

## 9. 代码开发规范（强制）

### 9.1 连续 5 个报错退出机制

进行代码开发工作时，如果当前采用的方法遇到 **连续 5 个报错**，**必须**：

1. **立即退出自动模式**
2. **重新审视当前解决方法本身**（不是补丁，是质疑方法）
3. **生成一份简要 debug 报告**：
   - 已尝试的方法
   - 每个方法的报错摘要
   - 怀疑的根因（不是症状）
   - 建议的下一步方向（**不**是直接给出答案）
4. **等待人工手动确认**才继续

### 9.2 根因优先

打补丁 = 失败。出现 bug 时**先问"为什么"，再问"怎么办"**。

### 9.3 每次决策可追溯

每个决策（参数、模型选择、阈值）都要能在对应 dev_doc 中找到依据。

**csi-pose 特定的可追溯清单**（改这些参数时必须先有 dev_doc 支撑）：

- 跌倒 FSM 阈值（IMPACT、ALARM、hold 窗口）
- 时钟拟合参数（下凸包边距、对齐容差）
- 训练超参（窗口长度、batch size、loss 权重）
- 任何"我猜这样能行"的常量

### 9.4 基准会话复现

任何对阈值的调整或代码修改都必须**先**用 `fall-demo-01` 复现基线指标（10/11 召回、2 FP），再考虑新方案。**不能**直接套用 README 数字。

---

## 10. 任务规模管理

- 任何任务开始前，先评估**是否需要 brainstorm**：单文件小改可跳过；多文件 / 跨子系统 / 新功能**必须 brainstorm**
- 任务被中断后，**不要猜测进度**，先看 git、文件、task 列表核对
- 同一会话里如果用户已经改了方向，**不要延续旧路径**

### 10.1 csi-pose 跨子系统影响面

修改前必查：

| 改这里 | 至少要看 |
|---|---|
| `firmware/components/csi_link/` | TX/RX 全部、`host/csi_pipe/` 解析逻辑 |
| `host/csi_pipe/`（时钟拟合） | `train/` 数据加载、`rt/` 实时对齐 |
| `host/recorder/`（HDF5 格式） | `train/` 数据集类、`teacher/` 标签对齐 |
| 跌倒 FSM 阈值 | `rt/csi_rt/`、`configs/rt.yaml`、对应 dev_doc |
| `train/` 损失 / 模型 | `rt/csi_rt/` 模型加载、ONNX/TorchScript 导出 |

---

## 11. Claude 必须遵守的红线

1. ❌ **不动 LICENSE** — 上游版权信息原样保留
2. ❌ **不把单次会话的阈值当真理** — `fall-demo-01` 数字仅作起点
3. ❌ **不绕过时钟同步直接拼接数据** — 下包络是核心，不能简化
4. ❌ **不把 CSI 静止检测当作可用能力** — 当前 disabled，README 已声明
5. ❌ **不写未确认的方案** — 任何 dev_doc 在用户复核前都标注"待复核"
6. ❌ **不打补丁** — 出现连续 5 报错必须退出自动模式
7. ❌ **不复用旧路径** — 用户改方向后，旧的 dev_doc 必须明确标注"已废弃"
8. ❌ **不堆废话** — 输出必须有可决策性
9. ❌ **不超速率** — RPM < 200 / TPM < 10M

---

## 12. 当前项目状态速查

| 项 | 状态 |
|---|---|
| README / README.ko.md | ✅ 完整 |
| 5 个子系统代码 | ✅ 完整 |
| 配置示例（4 份 YAML）| ✅ 完整 |
| 跌倒 FSM 阈值基线 | ✅ 有（单会话）|
| 多会话验证 / 跨用户验证 | ❌ 未做 |
| CSI 静止检测 | ⏸ 暂关 |
| 单元测试 / CI | ⏸ 待核对 |

**下一步（如需推进）**：
1. 进入 `rt/csi_rt/` 核对跌倒 FSM 实现位置
2. 在 `train/` 跑一次 `fall-demo-01` 复现基线
3. 写 `dev_doc/1-csi-pose-codebase-walkthrough-2026-06-26.md` 把代码与决策对齐

---

**最后更新**：2026-06-26 by Claude
**依据**：父项目 `ESP32_FallRec/CLAUDE.md` 格式 + `csi-pose/README.md` 内容
