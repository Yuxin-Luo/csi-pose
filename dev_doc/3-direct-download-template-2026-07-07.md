# 决策记录：direct_download 模板方案

> 日期：2026-07-07
> 背景：用户反馈原项目的串口配置流程（SET_IDX / SET_CH / START）每次烧 6 块板都需要重复操作，太繁琐。希望有"改 1 个数字 → flash → 完成"的工程。
>
> 相关文档：
> - [1-reproduce-tutorial-2026-07-07.md](1-reproduce-tutorial-2026-07-07.md)
> - [2-esp32-flashing-deep-dive-2026-07-07.md](2-esp32-flashing-deep-dive-2026-07-07.md)
> - 上游代码：[firmware/tx/main/main.c](../../firmware/tx/main/main.c)、[firmware/rx/main/main.c](../../firmware/rx/main/main.c)

---

## 决策内容

新建 `firmware/direct_download/` 目录，提供 2 个 ESP-IDF 工程模板：
- `direct_tx/` — 3 块 TX 板共用，烧不同 idx 只改 `#define BOARD_IDX`
- `direct_rx/` — 3 块 RX 板共用，烧不同 idx 只改 `#define BOARD_IDX`

特性：
- 编译时常量 idx / ch 写死 → 烧完上电即运行
- 自动启动 TX 广播、自动切换 RX 到 stream 模式
- 仍然写回 NVS（保持配置可观测、一致性）
- 仍保留 `HELLO` / `STATUS` / `STOP` console 命令（TX 板能 2 次调试）
- 共用上游 `firmware/components/csi_link/`（通过 `EXTRA_COMPONENT_DIRS` 引用，**不重复**）

## 替代方案对比

### 方案 A：6 个独立工程（`tx0/`、`tx1/`、…、`rx2/`）

- ✅ 一次编译、一次烧写
- ❌ 上游更新需要同步到 6 处
- ❌ 6 个 build/ 目录，磁盘占用 ≈ 3.6 GB
- ❌ 用户提问"如何在 6 个工程间 diff"

### 方案 B（最终采纳）：2 个模板工程 + #define 参数化

- ✅ 一次编译、一次烧写（烧完上电即用）
- ✅ 上游更新只需同步 2 个 main.c
- ✅ 2 个 build/ 目录，磁盘占用 ≈ 1.2 GB
- ✅ 用户每块板只改 1 个数字
- ✅ README 直接给"6 块板参数对照表"
- ❌ 改 idx 后需要重新编译（但增量编译 < 30s）

### 方案 C：保持原版串口配置

- ✅ 模板与上游 1:1 对应，零同步成本
- ❌ 每次烧完要敲 ~4 条串口命令（SET_IDX / SET_CH / START / 验证）
- ❌ 6 块板 × 4 条命令 = 24 次输入；敲错可改，但疲劳下易错

---

## 实现细节（与原版的关键 diff）

### TX：`direct_tx/main/main.c`

```c
// 新增编译时常量
#define BOARD_IDX 0              /* 【改这里】tx0=0, tx1=1, tx2=2 */
#define BOARD_CH  6              /* 【改这里】所有板必须一致 */

void app_main(void) {
    ESP_ERROR_CHECK(csil_cfg_init());
    s_boot_id = csil_cfg_next_boot_id();

    // 与原版区别 ①: 不再从 NVS 读 idx/ch, 直接用 #define
    s_idx = BOARD_IDX;
    s_ch  = BOARD_CH;
    csil_cfg_set_u8("idx", s_idx);   // 写回 NVS 保持一致性
    csil_cfg_set_u8("ch", s_ch);

    csil_serial_init();
    banner();

    ESP_ERROR_CHECK(csil_wifi_start(s_ch));
    ESP_ERROR_CHECK(csil_espnow_tx_init());

    const esp_timer_create_args_t targs = {.callback = tick_cb, .name = "txbeacon"};
    ESP_ERROR_CHECK(esp_timer_create(&targs, &s_tick));

    // 与原版区别 ②: 自动启动, 不靠 START 命令
    s_rate = 100;
    s_seq  = 0;
    s_sent_ok = s_sent_fail = 0;
    s_running = true;
    schedule_next();

    csil_console_run(handle);  // 不返回
}
```

### RX：`direct_rx/main/main.c`

```c
#define BOARD_IDX 0
#define BOARD_CH  6

void app_main(void) {
    ESP_ERROR_CHECK(csil_cfg_init());
    s_boot_id = csil_cfg_next_boot_id();

    s_rx_id = BOARD_IDX;
    s_ch    = BOARD_CH;
    csil_cfg_set_u8("idx", s_rx_id);
    csil_cfg_set_u8("ch",  s_ch);

    csil_serial_init();
    banner();

    ESP_ERROR_CHECK(csil_wifi_start(s_ch));

    s_q = xQueueCreate(QUEUE_DEPTH, sizeof(csi_item_t));
    configASSERT(s_q);
    ESP_ERROR_CHECK(csi_rx_init(s_q));

    BaseType_t ok = xTaskCreate(framer_task, "framer", 4096, NULL, 5, NULL);
    configASSERT(ok == pdPASS);

    // 与原版区别: 自动进 stream 模式, 不靠 START 命令
    s_mode = M_STREAM;
    csil_set_binary(true);

    csil_console_run(handle);  // 不返回
}
```

### 共用组件

`direct_tx/CMakeLists.txt` 与 `direct_rx/CMakeLists.txt` 顶部：

```cmake
list(APPEND EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../../components")
```

这样 `csi_link` 组件仍然只有一份。

---

## 6 块板烧录参数（按板汇总）

| 板 | main.c 改 | project() 改 | 烧写命令 |
|---|---|---|---|
| TX0 | `BOARD_IDX 0` | `project(csi_tx0)` | `idf.py -p COMx flash` |
| TX1 | `BOARD_IDX 1` | `project(csi_tx1)` | 同上 |
| TX2 | `BOARD_IDX 2` | `project(csi_tx2)` | 同上 |
| RX0 | `BOARD_IDX 0` | `project(csi_rx0)` | 同上 |
| RX1 | `BOARD_IDX 1` | `project(csi_rx1)` | 同上 |
| RX2 | `BOARD_IDX 2` | `project(csi_rx2)` | 同上 |

`BOARD_CH 6` 6 块全部相同，不要瞎改。

---

## 风险与未决问题

1. **RX 直烧版无 console 二进制 / ASCII 切换** — 烧完直接进 stream，串口只看到 CSI 帧。需要在 conhost bridge 之前用 raw log 验证。
   - 缓解：`csil_set_binary(true)` 之前打了 `banner()`，所以第一时间至少能看到 `BOOT role=rx idx=N mac=... ch=6 ...`。然后下次烧的可能就跳过了（banner 占 1 个 USB 写周期）

2. **如果 6 块板的 TX 板 idx 撞了**（例：烧错 direct_tx/csi_tx1 但忘了改 `BOARD_IDX 1` → 实际 BOARD_IDX 仍是 0）— RX 端通过 `tx_idx` 字段不识别，丢帧。
   - 缓解：README §5 给出了 `STATUS` 命令验证方法 + `per_tx` 计数验证法

3. **上游 `firmware/tx/main/main.c` / `firmware/rx/main/main.c` 更新后需要手工同步** — 例：M1 修了 `tick_cb()` 漏帧 bug，需要 patch 到 `direct_tx/main/main.c`。
   - 缓解：上游的 diff 通常是局部函数内的修改，main.c 的 diff 通常 < 30 行。定期手动 `diff` 同步。
   - 反例：如果上游改了 `app_main()` 的启动顺序，patch 会比较复杂。

4. **`BOARD_IDX` 写错值（例如误写 5）** — WiFi CSI 仍能工作，但 `tx_idx` 字段不合规；RX 收到后丢弃（`csi_rx.c` 的 `tx_idx < 3` 检查）。
   - 缓解：唯一性 + 校验 — 检查每块板的 banner 输出 `idx=0/1/2`，错则返工。

---

## 自检结果

- ✅ 6 个板只用 1 套模板
- ✅ 烧写前只改 2 个 #define 参数
- ✅ 烧完上电即用，无需串口命令
- ✅ 共用 `firmware/components/csi_link/`，磁盘占用减半
- ⚠️ RX 直烧版丢失 console 调试能力（仅保留 banner 输出）
- ⚠️ 上游变更需要手工同步（成本 < 30 行/diff）

## 后续动作（待执行）

- [ ] 第一次真实编译 — 用户在自己机器上跑 `idf.py build`，反馈是否通过
- [ ] 第一次真实烧写 — 烧 1 块 TX0 + 1 块 RX0，验证链路
- [ ] 检查 README §5 验证方法是否真能区分 TX0/1/2

---

## 参考链接

- 上游 TX 源码：[firmware/tx/main/main.c](../../firmware/tx/main/main.c)
- 上游 RX 源码：[firmware/rx/main/main.c](../../firmware/rx/main/main.c)
- 共享 csi_link 组件：[firmware/components/csi_link/](../../firmware/components/csi_link/)
- 直烧版 README：[firmware/direct_download/README.md](../../firmware/direct_download/README.md)
