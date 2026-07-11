# 11 — Loss Gap Investigation (物理层)

**状态**：⏸ 调查中（2026-07-11）
**触发**：s01-smoke recording 末态 Loss 95%（链路 0-0 rx=6898 lost=145694）
**目的**：找出 9 链路同时间点"集体断联 ~18.7min"的物理层根因

---

## 1. 现象描述

### 1.1 实测数据（s01-smoke rawlog 扫描）

| TX | rx 帧数 | seq 跨度 | 最大 gap | gap frames |
|---|---|---|---|---|
| TX0 | 7021 | 152712 | 3376676865* | 1 真 gap + 1 wrap |
| TX1 | 6909 | 152770 | 145586 | 1 大 gap |
| TX2 | 6649 | 152708 | 145536 | 1 大 gap |

\* `3376676865` 是 RX 解析错位导致（frame 字段错位 + checksum 通过）；非真实 gap。

### 1.2 关键事件：3 块 RX 同时间点集体断联

rawlog 扫描发现 3 块 RX 在 **同一时刻**出现大 gap：

| TX | 断联前最末 seq | 断联后首 seq | gap 大小 | t_curr (host ns) |
|---|---|---|---|---|
| TX0 | 156571 | 302097 | 145526 | 18318569406238154695 |
| TX1 | 156315 | 301901 | 145586 | 18318569401943187224 |
| TX2 | 156137 | 301673 | 145536 | 18318569406238161064 |

**3 块 RX 的 t_curr 在 64ns 之内**——这是物理层同步事件（同一 WiFi 帧时间槽）。
boot 启动时间 22:39:31 CST，"t_curr" 折算 host time ≈ boot 后 30-40s 期间。

**loss = 18.7 min 的 TX sequence 全部丢失**，不是长时间丢失，是 **一次性 all-links-down event**。

### 1.3 推断

3 个独立的 RX 同时 + 同一时刻出 gap ⇒ **不可能是 3 块 RX 各自独立 wifi 失联**。

可能共同原因：
- (a) **TX 端 esp_timer 临时挂起** —— 但 3 个 TX 独立硬件，不可能同时挂
- (b) **WiFi AP / 信道冲突** —— 一次性所有信道被占用？或 esp-now peer key reset？
- (c) **host USB hub 抖动** —— 一次性所有 RX USB 端口掉电？短暂？
- (d) **ESP-NOW 注册 reset** —— 3 块 ESP32 (TX0/1/2) 集体 peer 失效 + 重注册
- (e) **桥接层 (host bridge.py) 集体阻塞** —— 例如 mosquitto 写盘阻塞、或者 KVM/snapshot 触发

数据丢了，**next frame seq = ~302000** 说明 TX 在 18.7min 期间**继续发包**（s_seq 持续增长），但 RX 在 18.7min 内一个都没收到。

---

## 2. 当前 LinkTracker 行为（重新确认）

`host/csi_host/gap.py LinkTracker.update(seq)`：
```python
def update(self, seq):
    if self._last is not None:
        if seq > self._last + 1:
            self.lost += seq - self._last - 1
        elif seq <= self._last:
            self.resets += 1
    self.received += 1
    self._last = seq
```

**first update (after last None)** → received=1, lost=0 ✓ (user 要的"从第一个有效帧 baseline"已经实现)。

**所以修改 LinkTracker 解决不了 95% loss** —— lost=145694 是真实丢包，不是 startup 计帐。

---

## 3. 调查计划（dev_doc/11 plan）

### 3.1 0次过：复现 + 同时间点核查

```bash
# 短期 plan：只跑 30s，但保证 TX 已跑 5min+ 之后开始录制
# （上次触发条件可能需要 TX 长时间运行 + WiFi 干扰叠加）
./host/boot_recording.sh test s01-stress
```

在 30s 内大概率**不重现**问题（问题需要 30s 后才出现，可能与 WiFi chip thermal 有关）。

### 3.2 1次过：RX USB dmesg 调查

```bash
# 看 boot 期间 RX 是否有 USB disconnect/reconnect
sudo dmesg -w | grep -E 'ttyACM|usb|cdc_acm|xhci'
```

如果发现 RX USB 掉电再恢复，方向 = host hardware issue。

### 3.3 2次过：ESP-NOW 注册核查

```bash
# 用 ESP32 console (有 USB 串口的 TX) 检查 STATUS 输出是否同时间点突变
# 串口监听抓 TX 端 boot_id 变化
# 如果 TX 自身 reboot (s_seq 重置为 0 + boot_id += 1)，则证明 TX 端重启
```

**TX s_seq 跳到 152000 是从 0 持续运行 ~19min 的累计**。如果 TX0 也 reboot，s_seq 会重置为 0，RX 收到的 seq 会从 0 开始 → **但 live.log 显示 resets=0** ⇒ TX 没 reboot。

### 3.4 3次过：串口缓冲检查

bridge.py 的串口 read 是 blocking。在 USB2 + Linux 下，串口 FIFO 满时 read() 阻塞等。

**如果 bridge 进程在 polling Mosquitto 或 rawlog write 时主线程 blocked**，串口 read 跟不上，USB CDC ACM buffer overflow → 串口丢帧（host-side kernel buffer 满）。

检查方法：
- strace -p $(pgrep -f bridge.py | head -1)
- 看 read/write system call 频率

### 3.5 4次过：ESP-NOW 长跑稳定性

3 块 TX 是独立 ESP32，每块以 ~130pps 发 ESP-NOW 广播。如果 ESP-NOW 协议层在 18 min 左右有 silent reset（peer key rotation / AP-side kick），3 块会同时失联。

检查方法：
- 长期 (1h+) TX-RX 单链路跑，记录 loss 是否在某个时间点集**体跳
- 对比：loss 是渐增（RF 正常衰减）还是阶梯式突变（事件触发）

---

## 4. 给 dev_doc/11 调查结果的格式

每完成一次排查，记录到本文件 §5 报告，格式：

```
### X.1 复现：[一次失败的复现尝试]
- 命令：./host/boot_recording.sh test s01-stress
- boot 时长：30s
- live.log 末尾 loss：链路 X-Y loss=XX% max_gap=YYYY
- 结论：未复现

### X.2 USB dmesg
- 命令：sudo dmesg -w
- 关键事件：22:40:00 usb 1-2: USB disconnect, device 5
- 影响 RX：rx0 (port 1-2)
- 解释：USB 总线级掉电
```

---

## 5. 调查结果（待填）

*暂无* — 待执行 §3 计划

---

## 6. 相关决策追溯

| 决策 | 来源 |
|---|---|
| LinkTracker 不需要改 | 当前逻辑 first frame _last=None → skip gap，对应 user 需求 |
| 95% loss 真实存在 | rawlog seq 跨度证明 |
| 物理层调查优先于 firmware | 3 块 RX 同时间 + 同 TX s_seq 持续 → 共因 |
| 留 dev_doc/11 跟踪 | 避免一次性猜测 patch，遵循根因优先（CLAUDE.md §9.2）|

---

## 7. 不在本次修改

- ❌ **不修 LinkTracker** — first update 已是正确行为
- ❌ **不修 firmware TX** — seq 持续增长说明 TX 在跑
- ❌ **不修 mqtt_recorder** — 它只是 mirror bridge.py 行为

**等待**：物理层 (USB / WiFi / ESP-NOW) 决定再 fix 哪一层。

---

**维护者**：Claude
**依据**：s01-smoke rawlog 扫描 + user 2026-07-11 22:30 反馈"loss 重新 baseline" + dev_doc/5 §3.2 startup artifact 已确认不适用
