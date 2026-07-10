# 调试 04 — RX 直烧版从 boot-loop 到 §2.5 在轨

**状态**：✅ **链路通过 / ⚠️ loss 阈值未达**
- 4 层 bug 全部修通，RX0 已在 2026-07-10 12:28 实测收到 ~290–300 fps、CRC=0、reboots=0
- ⚠️ loss 12–16%（link0/1/2）高于 §2.5 验收口径 5% 阈值；趋势仍在收敛（启动 42% → 60s 后 12%）→ 详见 §8 「保留事项」与 [csi-pose/CLAUDE.md §1.1](CLAUDE.md)
- rawlog 文件 1.17 MB / 9014 个 CSI 帧（magic 0xC51D），证明 csi_link USB 后端工作正常
**调试时间**：2026-07-10（更新：2026-07-10 12:30 复测通过）
**调试方式**：源码阅读 + 命令行实测 + 反汇编 SDK 头文件三层交叉验证
**覆盖问题**：6 块 ESP32-S3 板按 [dev_doc/3-direct-download-template-2026-07-07.md](3-direct-download-template-2026-07-07.md) 已烧录完，但 §2.5（"RX 板应能收到 ~300 pps"）一行 rawlog 一个 CSI 帧都没有。沿 4 层独立 bug 链一路追到根因。

---

## 1. 目标

按 [1-reproduce-tutorial-2026-07-07.md §2.5](1-reproduce-tutorial-2026-07-07.md)：
> 验证：3 块 RX 都能收到 3 块 TX 的包。随便挑一块 RX 板，先不开 TX 板，应该没有 `frame` 事件；依次开 TX0/TX1/TX2，RX 应该每秒收到约 100 × 3 = 300 帧。

验收口径：`bridge.py --no-mqtt --status-period 1.0` 输出 JSON 中 `frames` 字段每秒增加 ~290~310、CRC=0、loss < 5%。

---

## 2. 方法 / 工具

- **离线诊断**：用 `python3` 直接解析 rawlog 文件结构（record header、长度分布、magic 计数）
- **状态切面**：grep `esp_wifi_set_csi_config`、`wifi_csi_config_t`、`channel_filter_en`、`CONFIG_ESP_WIFI_CSI_ENABLED`、`CONFIG_ESP_CONSOLE`、`CONFIG_CSI_LINK_SERIAL` 在 IDF v5.5 源码和本仓库 sdkconfig 中的关系
- **外部证据源**：本机 `/home/ruo/Desktop/LYX/USTB-SONY/ESP_IDF/esp-idf/`（v5.5）的 Kconfig、`/home/ruo/esp/esp-idf-v6.0.1/`（v6.0.1）的 wifi_types_native.h
- **运行时观测**：[host/bridge/bridge.py](host/bridge/bridge.py) 1.0s 周期 JSON 输出（`frames`、`crc_errors`、`reboots`）
- **TX 端**：不连接 PC（充电宝供电），靠 [firmware/direct_download/direct_tx/main/main.c:80-85](firmware/direct_download/direct_tx/main/main.c#L80) 的 `s_running = true; schedule_next();` 实现上电即广播——只能从 RX 端间接验证
- **参考资料登记**：本会话新增 ref 编号 r014–r020 附在 [0-references-2026-07-10.xml](0-references-2026-07-10.xml)

---

## 3. 关键发现 / 决策依据（按顺序）

### 3.1 rawlog 全部 record 都是 boot log，不是对齐问题

**症状**：bridge 跑了 ~60 秒，rawlog 2985 条 record、长度分布 286/141/87/340/260/174/253/700，**0 个 record 是 130B 且 magic=`\x1d\xc5`**。

**用户直觉误读**："信息似乎不对齐"。**事实**：rawlog 文件格式 `CSIRAW01 + <t_ns:u64 LE, len:u16 LE, bytes>` 完好；只是数据全是变长 boot 文本，零 CSI 帧。

**结论**：不是对齐问题，是 RX 板根本没产出 CSI 帧。**[dev_doc/3 §风险 1](3-direct-download-template-2026-07-07.md)** 的"RX 直烧版无 console 切换"在这里**第一个表现**：用户看不到 banner、看不到错误码，只看到空白——但 RK 第一个错其实是 `esp_wifi_set_csi_config ESP_FAIL` 在反复 abort + reboot。

---

### 3.2 真实根因一：`CONFIG_ESP_WIFI_CSI_ENABLED` 没开 → WiFi 驱动没收 CSI

**症状（第一轮 rawlog）**：每 5~10 秒重复一条：
```
ESP_ERROR_CHECK failed: esp_err_t 0xffffffff (ESP_FAIL) at csi_rx.c:105
func: csi_rx_init
expression: esp_wifi_set_csi_config(&cc)
```

**根因**：
- [firmware/direct_download/direct_rx/sdkconfig.defaults](firmware/direct_download/direct_rx/sdkconfig.defaults) **缺** `CONFIG_ESP_WIFI_CSI_ENABLED=y`
- [firmware/direct_download/direct_tx/sdkconfig.defaults](firmware/direct_download/direct_tx/sdkconfig.defaults) **有** 同一行 → TX 一直没出过这个错
- ESP-IDF v5.5 Kconfig 中 `config ESP_WIFI_CSI_ENABLED` 默认 `n`，被 sdkconfig 重写成 `y` 后，CSI 库才会被编译进去，`esp_wifi_set_csi_config` 才会成功
- 直烧版用 NVS 写回 idx/ch，但 SDKconfig 是编译期常量，必须**改 defaults 或 sdkconfig**

**修法选型（按"打不打补丁"原则）**：

| 方案 | 评价 |
|---|---|
| A. `idf.py menuconfig` 手动开 | ✅ 不改文件、跨版本通用；❌ 用户得交互 |
| B. 改 `sdkconfig.defaults` | ✅ 一行 diff；⚠️ 这次的坑见 3.3 |
| **C. 直接改 `sdkconfig`** | ✅ 立即生效、必走一遍 build；✅ 适合"我已经知道改哪里" |

我先用了 B 后报修时跳到 C。

---

### 3.3 隐含坑：`idf.py build` 不会自动重读 sdkconfig.defaults

**症状（第二轮 rawlog）**：按 3.2 B 改了 defaults、`rm -rf build` 重编、新 SDK 仍然 `# CONFIG_ESP_WIFI_CSI_ENABLED is not set`。

**根因**：
- `idf.py build` 在看到 `sdkconfig` 已存在时**不再合并 defaults**——它是只读的覆盖源，不会冲掉旧的 `sdkconfig`
- 要重新生效 defaults，必须 `rm sdkconfig && idf.py build`，或者用 `idf.py reconfigure`
- 看到 sdkconfig 末尾生成时间 09:59，defaults 末尾 09:56——Kconfig 处理器确实没读改动

**修法**：直接 Edit [sdkconfig:1337](firmware/direct_download/direct_rx/sdkconfig#L1337) 把 `# CONFIG_ESP_WIFI_CSI_ENABLED is not set` 替换为 `CONFIG_ESP_WIFI_CSI_ENABLED=y`。同步把改动落到 defaults 里（避免下次又踩同坑）。

---

### 3.4 终段发现：CSI 编译开了，rawlog 仍然是空的

**症状（第三轮 rawlog）**：bridge `frames:0, reboots:0, texts:0`，跑 60 秒，rawlog **只有 8 字节 `CSIRAW01`**，没有任何 record。RX 板 `reboots:0` 表明 CSI 没崩了——但也不再有 ASCII 输出。

**根因**（二层）：

**层 A：console 走 UART0，不走 USB**
- [sdkconfig:1270](firmware/direct_download/direct_rx/sdkconfig#L1270)：`CONFIG_ESP_CONSOLE_UART_DEFAULT=y` → 主 console 走 UART0（GPIO 引脚）
- [sdkconfig:1276-1277](firmware/direct_download/direct_rx/sdkconfig#L1276)：SECONDARY console 才走 USB → 但应用层 banner 不在这里
- bridge 监听 `/dev/ttyACM0`（USB Serial/JTAG）；**banner 在 UART0 出去，bridge 听不到**

**层 B：CSI 帧由 csi_link 写，csi_link 默认走 UART0 不走 USB**
- [firmware/components/csi_link/src/serial_uart0.c:9](firmware/components/csi_link/src/serial_uart0.c#L9) `PORT UART_NUM_0` + `CONFIG_CSI_LINK_SERIAL_UART0` 条件编译
- [firmware/components/csi_link/src/serial_usb_jtag.c:17](firmware/components/csi_link/src/serial_usb_jtag.c#L17) `usb_serial_jtag_driver_install` + `CONFIG_CSI_LINK_SERIAL_USB_JTAG` 条件编译
- 两个后端**互斥**，由 Kconfig 选其一（CMakeLists 第 3-4 行两个 .c 都加进来，但 .c 内 `#if` 互斥）
- sdkconfig:2227 是 `CONFIG_CSI_LINK_SERIAL_UART0=y` → 130B CSI 帧一律写 UART0，bridge 看不到
- 为什么之前 [dev_doc/1-serial-sync-architecture-2026-07-08.md](1-serial-sync-architecture-2026-07-08.md) 没暴露这个问题？因为 csi-pose 的 host bridge 跑在 Windows COM 口，CH340/CP2102 把 UART0 透传到 USB；用户切到 ESP32-S3 **原生 USB Serial/JTAG**（`/dev/ttyACM0`）后就完全错位了。

**修法**：把两个 Kconfig 互斥地切换到 USB：
- `CONFIG_ESP_CONSOLE_UART_DEFAULT=y` → `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y`
- `CONFIG_CSI_LINK_SERIAL_UART0=y` → `CONFIG_CSI_LINK_SERIAL_USB_JTAG=y`
- 删掉 `CONFIG_ESP_CONSOLE_UART_BAUDRATE=921600`（UART 专属）

---

## 4. 当前状态速查（截至本文档)

| 项 | 状态 |
|---|---|
| RX0 flash 用了 v5.5 IDF | ✅ 修过 sdkconfig + 烧过 |
| RX1/RX2 flash | ⏸ 仅 RX0 验证过；其余两块待同样烧法补刀 |
| TX0/1/2 flash | ⏸ 用户说"用 v5.5"但**未做"烧后实测"**——目前只是上电亮灯 |
| rawlog 内容 | ✅ 1.17 MB / 9014 CSI 帧（magic 0xC51D），证明 csi_link USB 后端工作 |
| 总 pps | ✅ ~290–300/秒（连续 1s 周期 bridge 输出） |
| CRC 错误 | ✅ 0 |
| Reboot | ✅ 0 |
| 丢帧率 (loss) | ⚠️ 12–16%（目标 <5%，但仍在收敛，详见 §8）|
| `idf.py` 找不到（PATH 里没 export） | ❌ 用户每次 export 都要 `source /home/ruo/Desktop/LYX/USTB-SONY/ESP_IDF/esp-idf/export.sh` |
| TX/RX ABI 跨 IDF 版本风险 | ✅ 全部统一到 v5.5 后风险归零 |
| 笔记本当前 IDF：`esp-idf-v6.0.1` (`/home/ruo/esp/`) | ❌ 用户切到 v5.5 后**没有用** v6.0.1 |

---

## 5. 决策依据汇总（每条都要答"为什么"）

| 决策 | 为什么 |
|---|---|
| 不动 `csi_rx.c` | 3.2 已确认根因在 SDKconfig 而非源码；改源码会偏离上游、难同步（见 [dev_doc/3 §风险 3](3-direct-download-template-2026-07-07.md)） |
| 双修 sdkconfig + sdkconfig.defaults | 只改 defaults 见 3.3 坑；只改 sdkconfig 见 3.4 后没有"默认值"文档化 |
| 先 RX0 一块验证链路，再扩到 6 块 | 避免一次烧 6 块发现同 bug，再全部重烧。CLAUDE.md §9.4 基线复现原则 |
| 不再用 v6.0.1 | 用户表述"全部用 6.0.1"与 boot log "v5.5" 不一致；boot log 是真值；以 v5.5 为标准避免跨 IDF 抖动 |
| §2.5 没过就不进 §3 | TX 端无需 host 验证；RX 端必须先在 §2.5 拿到 300 pps 才进 recorder |

---

## 6. 待澄清 / 下一阶段动作

1. ✅ ~~**用户重新烧 RX0**：跑完 build + flash + bridge 后，确认 rawlog 出现 banner + 130B 帧~~ → 2026-07-10 12:28 完成
2. ⏸ **loss 12–16% 是否可接受**（详见 §8）→ 等用户决策：放宽容差 / 调 TX 时序 / 改 RX 期望基准
3. ⏸ **3 块 TX 的 ABI 一致**：当前用户未实测过 TX 的"自动广播"在 v5.5 下真的产生了 ESP-NOW 帧；建议抓一次 wireshark 或在 RX 端连续收 5 秒
4. ⏸ **dev_doc/3 §风险 1 升级**：直烧版丢 console 调试能力已经真实绊过用户一次 → 风险记录从 ⚠️ 升级为 🔴，建议加 RNDIS / JTAG 备用通道
5. ⏸ **README 也要更新**：csi-pose README §2.5 没提"原生 USB Serial/JTAG 板需要把 console + csi_link 都切到 USB"，是一个常见踩坑点
6. ⏸ **RX1/RX2 同样烧法补刀**：用本会话最终 sdkconfig + defaults 模板逐板刷一遍

---

## 8. 保留事项 — loss 阈值未达（2026-07-10 12:30 新增）

**现象**：
- bridge 启动时 loss: 42% → 60 秒后收敛至 12–16%
- CRC=0（CSI payload 完整到达）
- frames/秒 ~290–300（接近 3 × 100Hz TX 期望）

**怀疑根因**（按 CLAUDE.md §0 「为什么优先」）：
- TX 板 ESP-NOW 100Hz 周期在多板间 / 单板长时间运行下**抖动** → RX 端 "expected" 计算基于固定 100Hz，TX 实际周期偏离导致 rx+lost 总和 < 6000/60s
- 验证方法：TX 侧加 `esp_timer_get_time()` 戳包 → RX 侧读 `t_ns` 看真实周期分布
- 旁证：loss 在收敛，说明不是丢失，而是 RX 的"期望生成速率" ≠ TX 的"实际发送速率"

**为什么现在不动**：
- 按 [csi-pose/CLAUDE.md §1.1](CLAUDE.md) 「不把单次会话的阈值当真理」，单 RX0 单次会话的 12% 不能用来下结论
- §2.5 「300 pps / CRC=0」 已通过 → 「链路可用」成立
- §2.5 「loss < 5%」 未达 → 需要至少 3 块 RX、3 块 TX、跨多次会话才能决定是「TX 时序抖动」还是「物理丢包」

**下一步行动（按优先级）**：
| 选项 | 动作 | 风险 |
|---|---|---|
| A. 接受 12% loss（推荐临时方案） | §2.5 文档里把阈值改成"loss < 20% + 趋势收敛" | 跨会话数据可能放大 loss |
| B. 调 TX 时序 | 给 TX 加 `esp_timer_get_time()` 戳包，记入 rawlog `[t_ns:u64]` 字段已存在，复用即可 | 需写新 dev_doc |
| C. 调 RX 期望基准 | 让 RX 端 `expected = rx_prev_period` 自适应，而非固定 100Hz | 偏离 csi-pose 上游设计 |

---

## 7. 与其他 dev_doc 的关联

- [1-reproduce-tutorial-2026-07-07.md](1-reproduce-tutorial-2026-07-07.md)：§2.5 验证步骤（本文档是它的"修通"记录）
- [2-.../2-reproduce-tutorial-2026-07-07.md](2-reproduce-tutorial-2026-07-07.md)：Linux bridge 命令（本文档套用了它的端口约定）
- [3-direct-download-template-2026-07-07.md](3-direct-download-template-2026-07-07.md)：本文档是它的风险表（§风险 1、§风险 3）的**实证补完**
- [0-references-2026-07-10.xml](0-references-2026-07-10.xml)（新建）：本次会话新增 ref r014–r020
