# direct_download — 上电即用的 ESP32-S3 直烧模板

> 目的：把原项目 `firmware/tx` 和 `firmware/rx` 改成 **"改 1 个数字 → build → flash → 完成"** 的工作流，**不要**再走串口 `SET_IDX` / `SET_CH` / `START` 配置。
>
> 适用：6 块板子只用一次、id 和信道固定的场景。

---

## 1. 目录结构

```
firmware/direct_download/
├── README.md                 ← 你正在读
├── direct_tx/                ← 3 块 TX 板 共用模板
│   ├── CMakeLists.txt        ← ★ 改 project(csi_txN) 的 N
│   ├── sdkconfig.defaults    ← (不必改)
│   └── main/
│       ├── CMakeLists.txt    ← (不必改)
│       └── main.c            ← ★ 改 BOARD_IDX (0/1/2)
│
└── direct_rx/                ← 3 块 RX 板 共用模板
    ├── CMakeLists.txt        ← ★ 改 project(csi_rxN) 的 N
    ├── sdkconfig.defaults    ← (不必改)
    └── main/
        ├── CMakeLists.txt    ← (不必改)
        ├── main.c            ← ★ 改 BOARD_IDX (0/1/2)
        ├── csi_rx.c          ← (从原 firmware/rx/main/ 复制, 不改)
        ├── csi_rx.h          ← (从原 firmware/rx/main/ 复制, 不改)
        └── sc_table.h        ← (从原 firmware/rx/main/ 复制, 不改)

firmware/components/csi_link/  ← TX 和 RX 共用, 通过 EXTRA_COMPONENT_DIRS 引用
```

> **共用 csi_link 组件**：两个项目都通过 `EXTRA_COMPONENT_DIRS="${CMAKE_CURRENT_LIST_DIR}/../../components"` 引用 `firmware/components/csi_link`，所以 `csi_link` 组件是同一份，任何修复只需要改一处。

---

## 2. 烧一块板要改 / 做哪些事（以 TX0 为例）

### 第 1 步：编辑 **两个** #define

打开 `direct_tx/main/main.c`，找到这一段：

```c
#define FW_VER "m0.1-d"
#define BOARD_IDX 0              /* 【改这里】tx0=0, tx1=1, tx2=2  */
#define BOARD_CH  6              /* 【改这里】6/1/11 都行, 全部 TX 板必须一致 */
```

**改成**：

```c
#define FW_VER "m0.1-d"
#define BOARD_IDX 0              /* tx0 = 0 */
#define BOARD_CH  6
```

### 第 2 步：编辑项目名

打开 `direct_tx/CMakeLists.txt`，最末：

```cmake
project(csi_tx0)
```

保持 `csi_tx0`（烧 TX0 用）。烧 TX1 时改成 `csi_tx1`，以此类推。

### 第 3 步：编译 + 烧录

```bash
cd firmware/direct_download/direct_tx
idf.py set-target esp32s3         # 只第一次需要
idf.py build                      # 首次 5–15 min, 之后增量 < 30s
idf.py -p COM7 flash monitor      # COM7 换成 TX0 对应的 USB 口
```

> ⚠️ **波特率**：
> - TX 和 RX 的 `sdkconfig.defaults` 都是 **921600** baud——跟上游 `firmware/tx`、`firmware/rx` 一致，TX 板也保持 921600（虽然 TX 输出很小但保持一致最简单）。
> - VSCode ESP-IDF 扩展的 monitor 默认 115200 跟 921600 不匹配 → 看到乱码。**修复方法：在项目根目录的 `.vscode/settings.json` 加一行 `"idf.monitorBaudRate": 921600`**，然后重启 monitor。
> - **PowerShell 直接 `idf.py monitor` 报 SyntaxError**？是 PowerShell 没继承 ESP-IDF 环境变量（Python 关联被 Node.js 抢了）。用以下任一方式：
>   - 用开始菜单的"ESP-IDF PowerShell"快捷方式开新终端
>   - 显式调 Python：``& 'C:\Espressif\tools\python\v5.5.4\venv\Scripts\python.exe' 'D:\ESP\.espressif\v5.5.4\esp-idf\tools\idf.py' -p COM7 -b 921600 monitor``

烧完后串口直接进 stream 模式，看不到 ASCII banner，**按 `Ctrl-C` 强退**，不破坏 flash。重新插 USB，TX 板立即开始广播。

---

## 3. 6 块板参数对照表（直接抄）

| 板 | 工程目录 | main.c 改 | CMakeLists.txt 改 | COM 口 |
|---|---|---|---|---|
| **TX0** | `direct_tx` | `BOARD_IDX 0` | `project(csi_tx0)` | （查设备管理器）|
| **TX1** | `direct_tx` | `BOARD_IDX 1` | `project(csi_tx1)` | … |
| **TX2** | `direct_tx` | `BOARD_IDX 2` | `project(csi_tx2)` | … |
| **RX0** | `direct_rx` | `BOARD_IDX 0` | `project(csi_rx0)` | … |
| **RX1** | `direct_rx` | `BOARD_IDX 1` | `project(csi_rx1)` | … |
| **RX2** | `direct_rx` | `BOARD_IDX 2` | `project(csi_rx2)` | … |

**信道**：`BOARD_CH 6` — 6 块板都用 6（按 README §Hardware 推荐），不要瞎改。

---

## 4. 标准工作流（推荐用 Git stash 隔离）

```bash
# 烧 TX0
sed -i 's/BOARD_IDX 0/BOARD_IDX 0/' direct_tx/main/main.c     # 已经是 0 时跳过
sed -i 's/csi_tx0/csi_tx0/' direct_tx/CMakeLists.txt
cd direct_tx && idf.py build && idf.py -p COM7 flash && cd ..

# 烧 TX1
sed -i 's/BOARD_IDX 0/BOARD_IDX 1/' direct_tx/main/main.c
sed -i 's/csi_tx0/csi_tx1/' direct_tx/CMakeLists.txt
cd direct_tx && idf.py build && idf.py -p COM8 flash && cd ..

# 烧 TX2
sed -i 's/BOARD_IDX 1/BOARD_IDX 2/' direct_tx/main/main.c
sed -i 's/csi_tx1/csi_tx2/' direct_tx/CMakeLists.txt
cd direct_tx && idf.py build && idf.py -p COM9 flash && cd ..

# 烧 RX0/1/2（同模式，目录改 direct_rx）
```

> ⚠️ **`BOARD_IDX 0` → `BOARD_IDX 1` 这种替换会先匹配上 `BOARD_IDX 0`**——所以最后一句用 `BOARD_IDX 1 → BOARD_IDX 2` 是对的。改 i 值的时候要保证前一个是 i-1。

或者如果你想更稳：直接用编辑器把 3 个值手动改掉，避免 sed 替换错位。

---

## 5. 怎么确认烧对了

### TX 板验证

烧完插 USB 接主机上电（先不要充电宝），开个串口 monitor：

```bash
cd direct_tx
idf.py -p COM7 monitor
```

虽然固件里没保留 SET_IDX 等命令（直烧版只保留 STATUS），但 **`BOOT` banner 会自动从串口打印**——因为我们在 `app_main()` 早期就 `csil_serial_init()` + `banner()`，那时还是 ASCII 模式。banner 之后立刻进 binary 模式（TX 实际上没切 binary，但 stream 控制权交给 esp_timer）。所以理论上应该能看到 banner。

> 验证技巧：看 banner 里 `idx=0`（或 1/2）是否对得上你烧的板。

### RX 板验证

RX 直烧版 **进入 binary 模式后才打 banner**——`csil_set_binary(true)` 在 banner 之后，固件默认行为。**所以串口上会先看到 banner**，然后才是流式 CSI 帧。

> 验证技巧：banner 里 `idx` 应该对得上。

### 现场联调（最有效）

任意烧好 1 块 RX，按上面 `STATUS` 命令（虽然默认进 stream 后也失效），**直接看 raw log**：

```bash
python host/bridge/bridge.py --port COM7 --rx-id 0 --no-mqtt --raw-dir logs
```

跑 10 秒后 `STOP` bridge，看 `logs/rx0-*.rawlog` 体积。如果体积在涨说明链路通了。然后跑 3 块 RX，看 RX0 的 `STATUS` 里 `per_tx=~/~/~` 三个数是否都 > 0。

---

## 6. 与原 firmware/tx, firmware/rx 的关系

```
firmware/tx/main/main.c       ← 原始, 需要串口命令配置
firmware/rx/main/main.c       ← 原始, 需要串口命令配置
                       │
                       ▼ (本目录 fork 出直烧版)
firmware/direct_download/direct_tx/main/main.c    ← 直烧版, 代码 99% 相同, 改 1 个 #define
firmware/direct_download/direct_rx/main/main.c    ← 直烧版, 代码 99% 相同, 改 1 个 #define
```

**上游固件升级时**：
- `csi_link` 组件升级不需要改 direct_download（共用）
- `firmware/tx/main/main.c` 或 `firmware/rx/main/main.c` 有改动时，需要手动同步到 `direct_download/*/main/main.c`
- 仅改了源代码不依赖组件 API 时，用 `diff` 对照，行号差 ≤ 5 行可手工同步

---

## 7. 故障排查速查

| 现象 | 大概率原因 | 解决 |
|---|---|---|
| 烧完看不到 banner | `BOARD_IDX` 改成了奇怪的数（>2） | 看 main.c 里有没有写 `tx_idx < 3` 校验 |
| `idf.py build` 找不到 `csi_link/wifi.h` | ESP-IDF 没 source | `source ~/esp/esp-idf/export.sh` |
| 烧完 RX 板没任何输出 | 可能 `csil_set_binary(true)` 在 banner 之前又被改 | 检查 main.c 的顺序（应: banner → csil_set_binary） |
| 3 块 RX 但 `per_tx=~/~/~` 不平衡 | TX 板 idx 串了 / 信道不一致 | 查所有 TX 的 `BOARD_IDX` 和 `BOARD_CH` |
| 烧完 `running.rst:0x10 (RTCWDT_RTC_RESET)` 死循环 | 看门狗没喂, esp_timer 没起 | 检查 `esp_timer_create` 和 `schedule_next` 是否被调用 |

---

## 8. 关键代码片段（不需要再翻原仓库）

### TX 自动启动关键 (`direct_tx/main/main.c`)

```c
// 在 app_main() 末尾:
s_rate = 100;
s_seq = 0;
s_sent_ok = s_sent_fail = 0;
s_running = true;
schedule_next();    // 启动 esp_timer, 第一次广播在 ~10ms 内触发

csil_console_run(handle);  // 不返回, 保留 console 用于 STATUS/STOP
```

### RX 自动流模式关键 (`direct_rx/main/main.c`)

```c
// 在 framer 任务创建后:
s_mode = M_STREAM;            // 让 framer 进入 stream 分支
csil_set_binary(true);        // 切换串口到二进制模式

csil_console_run(handle);     // 不返回
```

### NVS 一致性关键

每次启动都把 `BOARD_IDX` 和 `BOARD_CH` 写回 NVS：

```c
csil_cfg_set_u8("idx", s_idx);   // TX
csil_cfg_set_u8("idx", s_rx_id); // RX
csil_cfg_set_u8("ch",  s_ch);
```

这样即使有人后来用原版固件想 `SET_IDX`，状态也不会打架（虽然我们直烧版已经禁了 SET_IDX 命令，但 NVS 还保留着，可以让调试更友好）。

---

更多细节见：[dev-doc/3-direct-download-template-2026-07-07.md](../../dev-doc/3-direct-download-template-2026-07-07.md)
