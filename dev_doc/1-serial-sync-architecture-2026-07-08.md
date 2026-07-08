# 调研 01 — csi-pose 多源时间对齐架构（video↔CSI、跨板串口）

**状态**：⚠️ **待复核**（按 csi-pose/CLAUDE.md §11-1：未确认前不视为定稿）
**调研时间**：2026-07-08
**调研方式**：源码阅读（无 WebSearch / 无实测），依据全部来自 `ReferenceCode/Opensourse/csi-pose/` 本地仓库
**覆盖问题**：本次会话两个连带的 Q&A
1. 教师侧 video↔CSI 的"软对齐"是怎么做的，对摄像头硬件有什么约束
2. 跨 RX 板的串口间有没有同步，谁来对齐

---

## 1. 调研目标

把 csi-pose 处理**异构时间流**的两条机制讲清楚：

| 异构流 | 流 1 | 流 2 | 共同点 |
|---|---|---|---|
| 路径 A | 3 RX 板的 USB 串口 CSI | 单 webcam 的 USB 帧 | 都最终到同一 host |
| 路径 B | 3 RX 板互相之间 | — | 各板独立的 esp_timer + 各自 USB |

并回答两个根因级问题：
- 对摄像头像素/帧率有什么硬要求？
- 跨 RX 板的串口时钟是否做了显式同步？

## 2. 方法 / 工具

- 源码精读（不跑）：
  - `host/recorder/recorder.py`（入口）
  - `host/csi_pipe/mqtt_recorder.py`（MQTT 解包）
  - `host/csi_pipe/store.py`（HDF5 schema）
  - `host/csi_pipe/clockfit.py`（**核心**：每板时钟拟合）
  - `host/csi_pipe/align.py`（100Hz 网格）
  - `host/csi_pipe/samples.py`（build 编排，含关键注释）
  - `host/csi_pipe/align_verify.py`（验证口径）
  - `host/csi_host/unwrap.py`（每板 u32 单调化）
  - `teacher/teacher.py` + `teacher/csi_teacher/labels.py`（教师侧对齐容忍机制）
  - `firmware/{tx,rx,components/csi_link}/`（确认无硬件同步线）
- 一句话方法论：**不凭印象回答 Q，先读相关代码 + 找 fail-loud 注释 + 找代码中显式假设**。
- 参考资料登记：`0-references-2026-07-08.xml`

---

## 3. 关键发现

### 3.1 教师侧 video↔CSI：软对齐，不是帧锁

**做了什么**

- `recorder.py:21-44` 同时订阅 `csi/#`（3 块 RX 板通过 MQTT 中继）和 `cam/meta`（webcam publisher）
- 两条流都打**同一个 host 单调时钟** `time.time_ns()`：
  - CSI 侧：bridge 在 `unpack_csi` 时戳 `t_ns`，并存到 `/links/{rx}{tx}/t_ns`（`store.py:_LINK_FIELDS`）
  - 视频侧：webcam publisher 发 `{"t_ns": …, "frame_idx": …}` 到 `cam/meta`，bridge 转发，存到 `/video/t_ns, /video/frame_idx`（`store.py:append_video`）
- **没有硬件同步线**，没有帧触发器。两条流在 host 收包那一刻被同一个 time.time_ns() 戳了，靠这个共享基准做最近邻匹配。

**对摄像头硬件的硬约束**

- **不是像素约束，是"周界约束"**：
  - ✅ 摄像头必须**不动** —— `labels.py:42` 注释 "고정 카메라 가정"；`H,W` 在首帧 latch，移动会让所有 18 关节坐标错位
  - ✅ 必须**单人** —— ≥2 检测框直接 `STATUS_MULTI` 丢弃（`labels.py:30-32`）
  - ✅ 必须**够采到人** —— 实际 30fps 足够（RTMPose 训练 30fps，对 ≥10Hz 运动能恢复关节轨迹）
  - ❌ **不需要**和 CSI 100Hz 同步 —— `check_video_mapping()` 显式允许 `V < F`（`labels.py:88-93`）

**异步容忍机制**

| 异常 | 代码位置 | 处理 |
|---|---|---|
| 视频帧数 V < mp4 帧数 F（cam/meta 丢帧） | `labels.py:88-93` | 显式 warn，放行；靠 `frame_idx` 映射 |
| 单帧无人物 | `labels.py:30` | `STATUS_NO_PERSON` → pose18 写 `NaN`（`labels.py:48`） |
| 单帧多人物 | `labels.py:31-32` | `STATUS_MULTI` → pose18 写 `NaN` |
| CSI 丢 1-2 包 | `align.py:fill_gaps` | 线性插值 + `interp_mask=True` |
| CSI 丢 ≥3 包 | `align.py:fill_gaps` | 记入 `breaks`，训练样本整段失效 |
| 质量门 | `pam.py + qa.py` | `label_ok=False` 的窗口直接丢弃 |

**为什么这套架构站得住**

- 训练数据是"事后最近邻"匹配：CSI 100Hz 网格点 `tb` 往前找最近的 `/video` t_ns 取 pose
- 单个 pose label 在相邻 30ms 内被复用是允许的（30fps → 33ms 周期）
- NaN + mask 让模型训练时**自然跳过不可靠样本**，不用靠规则硬删

### 3.2 跨 RX 板串口同步：没有显式同步

> 这是这次的核心问题。结论先放出来：**没有硬件同步线，没有 host 层跨板联合时钟拟合**。

**做了什么 —— 分四层**

#### 层 1：硬件层 —— 无同步线

- `firmware/{tx,rx,components/csi_link}/` 全 grep：
  - 无 GPIO 触发线、无 PPS、无共享时钟输出
  - grep 出来的 GPIO 全是 `sdkconfig` 默认项，不是应用代码
- 板间唯一物理关系：**TX ESP-NOW beacon 广播** —— 所有 RX 板在同一物理瞬间（无线电传播 ~几十 ns）听到并采 CSI
- **含义**：3 块 RX 板各自有独立 esp_timer、boot_id、USB 串口，互相**完全不知道对方时间**

#### 层 2：固件层 —— 独立时间域

- 每板：`esp_timer` (u32 µs, 71.58 min wrap) + 自增 `boot_id`（启动一次变一次）
- 每板一个 USB-UART 到 host
- `host/csi_host/unwrap.py:TimeUnwrapper` —— 纯单板状态机，把 `esp_us` 单调化为无 wrap 序列，**没有任何跨板字段**

#### 层 3：Host 层 —— 每板**独立**时钟拟合

`samples.py:162-166` 是这次调研的**关键发现**，原注释直译：

> "보드(rx)별 클록핏 — 같은 보드의 3링크는 같은 클록"
> "**按板（rx）做时钟拟合 —— 同一块板的 3 条 link 共享一个时钟模型**"

也就是：
- 3 块 RX 板 → **3 个独立 `BoardClockModel`**（`clockfit.py:119`）
- 跨板之间**没有任何联合约束**
- 每板的 3 条 link（不同 TX）共享同一个 RX 时钟模型

每板怎么拟合：`clockfit.py:fit_board(esp_us, t_ns, boot_id)`

- 输入：板自己 `esp_us` + host `t_ns`（同一 host `time.time_ns()`，但每包 USB 延迟独立）
- 核心假设（`clockfit.py:1-3` 注释原文）：
  > "USB-UART 延迟只能让包**迟到**、不能早到 → (esp, t_host) 散点的**下包络**是无偏估计"
- 输出：t_fit = 该板的 esp→host 映射；resid_ns 反映 USB 抖动

**关键含义**：每板的 t_fit 是该板 esp→host 的**无偏估计**，但**三块板之间没有一致性约束**。

#### 层 4：训练数据层 —— 100Hz 公共网格 = 重采样，**不是**对齐

`align.py:grid_bounds`：

```python
def grid_bounds(streams, *, step_ns=10_000_000):  # 100Hz
    lo = max(int(s.t[0]) for s in streams)         # 取交集起点
    hi = min(int(s.t[-1]) for s in streams)         # 取交集终点
```

- 只是把 3 条 stream **共有的可用时间区间**对齐到 100Hz
- `align.py:grid_block` 在公共 `tb` 上对**每条 link 独立做线性插值**
- **这是 re-sampling，不是 alignment** —— 它没能力收紧跨板相位差，因为每条流的 t_fit 来自**不同的 `BoardClockModel`**

#### 层 5：验证层 —— 只验单板，不验跨板

- `align_verify.py:detect_gaps(t_fit_by_rx: dict, ...)` —— 按板检测 gap 后聚类，目的是和外部 `cmd_times` 比对时戳漂移，**不**是算板间相位差
- `clockfit.py:wrap_continuity` —— 验单板 u32 wrap 边界，**不**是跨板一致性
- **缺口**：csi-pose 当前没有"同一 beacon 时刻三板 t_fit 残差"的诊断工具

### 3.3 架构为什么能撑住 18 关节回归

虽然没有显式跨板同步，但 csi-pose 的训练目标**只对 ms 级相位差不敏感**，靠四个事实兜底：

| 兜底因素 | 数量级 | 含义 |
|---|---|---|
| TX beacon 物理同时性 | ε ~ 几十 ns（1m 内） | 同一瞬间所有 RX 采到的 CSI 来自同一信道 |
| CSI 帧周期 | ~10 ms（100Hz） | 网格分辨率 |
| USB 串口 jitter（单板） | p95 几 ms（看 fit_report） | **单板内**下凸包吸收偏置 |
| PAM 训练窗口 | 50 ms（5 帧） | 任务对 ms 级相位差不敏感 —— 信号（动作）远强于噪声（跨板残差） |

**为什么不进一步收紧跨板同步？**

因为 `fall-demo-01` 单次会话 10/11 召回 + 2 FP（`README.md`）已经够用。**任何想推 PAM 精度极限或换场景外推的优化，都得碰这一层**（详见 §4）。

---

## 4. 决策依据汇总

### 4.1 csi-pose 隐式跨板同步的"4 个事实"清单

| 编号 | 事实 | 出处 |
|---|---|---|
| F1 | 板间无硬件同步线 | `firmware/{tx,rx,components/csi_link}/` 全文 grep |
| F2 | 每板独立时钟拟合，无联合约束 | `samples.py:162-166` 注释 + `clockfit.py:fit_board` 签名 |
| F3 | 100Hz 公共网格只取交集 + 各自重采样 | `align.py:grid_bounds, grid_block` |
| F4 | 验证层只验单板 | `align_verify.py:detect_gaps` 入参是 `t_fit_by_rx: dict`，无板间判据 |

### 4.2 教师侧 video↔CSI 的"5 个不对称"清单

| 编号 | 不对称 | 处理 |
|---|---|---|
| A1 | 摄像头像素 / 分辨率 不影响 CSI | `labels.py:43-44` 只 latch 首帧 H,W |
| A2 | 视频帧率 ≠ CSI 帧率不需要相等 | `check_video_mapping()` 允许 V&lt;F |
| A3 | 视频丢帧不影响 CSI | `/video` 独立于 `/links`，NaN 容忍 |
| A4 | CSI 丢帧不影响视频 | `fill_gaps` 插值 + `interp_mask` 标记 |
| A5 | 摄像头动了 / 多人 → 训练样本直接废 | `STATUS_*` + `qa.gate_pass` &lt; 2% |

### 4.3 当前架构的 4 个真限制（不是设计缺陷，是权衡结果）

| 限制 | 触发条件 | 影响 |
|---|---|---|
| L1 | 单板 USB 频繁失去 beacon（resid &gt; 50 ms 持续） | 训练时该 link mask 大面积 True → 等效少了一个 RX 维度 |
| L2 | 三板 USB 抖动**严重异分布**（比如一个 USB hub、两个独立） | 跨板残差超出 PAM 容忍度 → fallback 是少用、或训练加权 |
| L3 | 单会话 fall-demo-01 的 10/11 召回 | 外推到不同身高 / 步态 / 房间必须重新标定（CLAUDE.md §1.1 诚实声明） |
| L4 | 摄像头采集侧不提任何"客观 0 时刻" | train/rt 没法用"动作真实开始"做 gold anchor，只能用最近 host 时戳 |

---

## 5. 如果要进一步收紧跨板同步，候选方案（用户未决策）

按 Agent Rules.txt 第 11 条：每个方案给难易 + 风险点。

| 方案 | 做法 | 难易 | 风险点 |
|---|---|---|---|
| **S1 联合多板时钟拟合** | 在 `clockfit.py` 增加约束：识别同一 beacon 在各板的帧，约束 `t_fit_A(t_beacon) ≈ t_fit_B(t_beacon)` | 中（要找同源 beacon，纯软件） | 需要 beacon→seq 的稳定映射；新增"跨板 anchor"通路要回归测单板 wrap_continuity |
| **S2 板间 GPIO 触发线** | RX 板额外接一根同步线，受 TX 或 host GPIO 触发 | 高（PCB + 固件 + 走线） | 触发线自身的延迟要单独测；破坏当前硬件拓扑 |
| **S3 TX 发 beacon 时给 host 旁路 trigger** | TX 多发一份 MQTT 或音频 ping；host 收到时记 `t_ns_zero` | 低（只改 host） | 只能给一个"绝对 0 时刻"，不能收紧板间相对相位 |
| **S4 PAM 输入前每板相位/时延校准** | 先验测每板 beacon→CSI 链路固定延迟，建一张 (rx, tx) 校准表 | 中（要走一次"空场"标定） | 标定自身的稳定性；温度漂移 |

**推荐起点**：S3（最低成本拿到"客观 0 时刻"，帮 PAM 训练锚定）+ 后续如果精度不够再加 S1。
**不推荐起点**：S2（破坏现有 6 板的简洁拓扑）。

---

## 6. 结论

### 6.1 教师侧 video↔CSI

- ✅ **Q：摄像头帧率/像素有约束吗？**
  A：**没有像素/帧率硬约束**，只要固定不动、单人、≥20fps 即可。CSI 是瓶颈，不要把摄像头往高帧率推。

- ✅ **Q：视频帧率与 CSI 怎么对齐？**
  A：**不帧锁，靠共享 host `t_ns` + 最近邻匹配**。两条流在同一 MQTT broker 的同一 host time time.time_ns() 下被戳，到训练 / 实时阶段做"最近 host 时戳取 pose"。

- ✅ **Q：一方更慢怎么办？**
  A：**显式处理**。视频慢 → `/video V < F` 走 frame_idx 映射；CSI 慢 → `fill_gaps` + break；缺人 → pose18 NaN；pam.py 用 `label_ok` 丢弃不可靠窗口。

### 6.2 跨 RX 板串口同步

- ✅ **Q：架构有做串口间同步吗？**
  A：**没有显式同步，没有联合拟合。**
  - 硬件层：无 GPIO/PPS/共享时钟
  - 固件层：每板独立 esp_timer，互不知对方
  - Host 层：**每板独立下凸包时钟拟合**（下凸包是无偏估计，但不是跨板一致）
  - 数据层：100Hz 公共网格是 re-sampling，不是 alignment
  - 验证层：只验单板，没有跨板一致性诊断工具

- ✅ **Q：为什么这套够用？**
  A：训练目标（18 关节 2D 坐标 + 站/躺 + 跌倒 FSM）对 ms 级相位差不敏感。兜底靠四个事实：TX beacon 物理同时性、100Hz 网格、每板下凸包吸收偏置、50ms PAM 窗口包含 ≥5 帧。

- ⚠️ **Q：哪里是隐含风险？**
  A：三板 USB 抖动**严重异分布** + 单会话标定 → 不能外推；任何想外推场景必须重新校准跨板残差。

---

## 7. 待澄清事项

1. **跨板残差实测量级** —— 当前文档基于代码逻辑，未跑 `host/csi_pipe/soak.py` 或 `align_verify.py` 量化。需要补一次"三板同时采 beacon 1 小时"实测，看 `t_fit` 残差的 p95。
2. **firmware 是否做 USB 批量聚合** —— 没读 `firmware/rx/main/*.c` 的细节。若 RX 把多个包 bulk 一次推上来，会影响 `t_ns` 戳的位置，影响 `clockfit.fit_board` 的假设。建议补读 `firmware/rx/main/` 全量源码。
3. **webcam publisher 实现** —— `host/csi_host/cam_core.py` 没读完整。需要确认 webcam 端的 `t_ns` 是 host 收帧时间戳还是摄像头内部时间戳，决定 ±几 ms 抖动。
4. **S1-S4 方案是否启动** —— 用户未决策。§5 列表待用户选序后再展开。

---

**最后更新**：2026-07-08 by Claude
**依据**：`Agent Rules.txt` 第 6/7/8/11 条 + `csi-pose/CLAUDE.md` §8/§11 + 当会话两个 Q&A 实证
