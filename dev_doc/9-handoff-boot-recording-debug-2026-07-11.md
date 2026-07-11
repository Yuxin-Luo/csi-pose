# 交接 09 — boot 录制系统 debug + handoff

**状态**：✅ **历史 handoff 已归档**——本文件记录 v1-v5 死因。
**时间**：2026-07-11 18:35（创建）→ 2026-07-11 22:20（更新）
**后续**：v6 EOFError 真根因已修（commit `91d09d3`），当前主线见 dev_doc/10。
**依据**：用户 2026-07-11 18:30 反馈（"幻觉严重、先梳理现状、必要时写 handoff"），
2026-07-11 22:00 反馈（"目前已经成功弹窗录制了，但需要 TEST mode"）

---

## 1. 现状一句话 (历史 — 已被 v6 commit 91d09d3 解)

`./host/boot_recording.sh` 主进程在 polling 循环**第一轮或第二轮内 silent 死亡** (v1-v3)；
到达 polling 之后在 cam/recorder 启动后又被 EOFError kill (v5/v6)。

**v6 真根因**：cam/recorder `input()` 在 `&` 后台进程 stdin 非 TTY 时 raise `EOFError`；
`set -e` 在 `wait` 返回非零时让主进程退出。Fix 见 commit `91d09d3`。

**v7 演进**：commit `5c8f722` 加 MODE 参数支持 test smoke + norm 训练两套独立流程。
**当前建议**：从 `./host/boot_recording.sh test s01-smoke` 60s 跑通验证后，再切 norm。
详见 dev_doc/10。

---

## (旧 §1 - 保留作 trace)

---

## 2. 已落地代码（4 个 commit，全在 main 分支）

| commit | 改动 | 行数 | 验证状态 |
|---|---|---|---|
| `e0f3f94` | feat: --start-on-key + --plan + overlay + plan.py | +110 | --help + parse + gate 单测过 |
| `a84f2ff` | feat: boot_recording.sh | +79 | bash -n 过，dry-run 到 preflight |
| `9323763` | fix: PYTHONUNBUFFERED=1 | +1 | 修了 buffering 但**没解决真正问题** |
| `b7a40a5` | fix: polling 加实时 frames 显示 | +5 | echo 一行都没机会打 → 死得太早 |

**未 commit 的本地修改**：无（所有改动已 commit）

---

## 3. 实测数据（关键）

### 3.1 最新 boot run（ts=20260711-182706）实际跑了 4+ 分钟

```
$LATEST=logs/boot-s01-r1-20260711-182706
-rw-rw-r-- 1 ruo ruo 4.3M  rx0.log    # 392 行 ≈ 4 分钟
-rw-rw-r-- 1 ruo ruo 4.3M  rx1.log
-rw-rw-r-- 1 ruo ruo 4.3M  rx2.log
```

- rx0 第 1 行 frames=1380，第 392 行 frames=**116,446**（增速 ~300 fps）
- 单链路 loss 从启动 95% 收敛到 ~22%（dev_doc/5 §3.2 startup accounting 伪影正在消退）
- rawlog 文件 15M，真实 CSI 数据完整

### 3.2 现在 3 个孤儿 bridge 还在跑

```
ruo 641917 3205 11 18:27 pts/5 python host/bridge/bridge.py --port /dev/ttyACM0 --rx-id 0 ...
ruo 641918 3205 11 18:27 pts/5 python host/bridge/bridge.py --port /dev/ttyACM1 --rx-id 1 ...
ruo 641919 3205 11 18:27 pts/5 python host/bridge/bridge.py --port /dev/ttyACM2 --rx-id 2 ...
```

PPID=3205（用户的 pts/5 shell），不是 init(1) —— 这点反常，通常孤儿会 reparent 到 init。可能 boot 是用 SIGKILL 被强杀的，bridges 没收到。

### 3.3 没有任何 polling 输出

按用户截图，boot 卡在 `Waiting for 3 bridges (frames > 280)...` 一行后**直接回 prompt**。但 rawlog 显示 bridge 实际跑了 4+ 分钟。

**唯一自洽的解释**：boot 在 `[ready=0]` 的 polling 第一轮就死了，所以 echo 那一行没机会打。**不是 user Ctrl-C**（user 没承认）。

---

## 4. 假设的 boot 主进程死因（按概率）

| # | 假设 | 证据 | 反证 |
|---|---|---|---|
| 1 | **`[ "$ready" -eq 3 ] && break` 在 ready=0 时返回非零 → set -e 触发 → 脚本退出** | bash 文档：`&&`/`||` 列表的退出状态是最后命令的，但列表整体退出码在 top-level 受 set -e 约束 | 没实测验证 |
| 2 | **`f=$(grep ... | tail -1)` 在 log 文件不存在 / grep 无匹配时 → pipefail → subshell exit 1 → set -e** | 但赋值在 set -e 下是豁免的 | 不太可能 |
| 3 | **terminal session pts/5 信号** | bridges 的 PPID=3205（pts/5），不是 init(1) | user 没主动关终端 |
| 4 | **`mosquitto` 或 `ttyACM*` 临时不可用** | 但 3 个 bridges 实际正常连上了（rawlog 有 15M 数据） | 不可能 |

**最优先验证**：假设 #1 —— 用 `set -x` 加 bash debug 跑一次，看 `[ "$ready" -eq 3 ] && break` 这行是不是真的触发了 set -e。

---

## 5. 数据产物现状

```
data/  - 仅 dev_doc/5 调试 1-4 目录（昨天 7-10 测试遗留），**无 s01-r1 录制**
logs/
├── boot-s01-r1-20260711-181753/  # 第 1 次 boot（用户报告卡死）
├── boot-s01-r1-20260711-182315/  # 第 2 次 boot（同样卡死）
├── boot-s01-r1-20260711-182706/  # 第 3 次 boot（同样卡死，但 bridges 跑了 4+ 分钟）
└── rx{0,1,2}-20260711-*.rawlog   # 多次失败的 rawlog（最大 15M，**真实数据**）
```

**所有 rawlog 数据可信，但都没接 recorder → 没有 s01-r1-*.h5 / *.mp4 产物**。

---

## 6. 用户 2026-07-11 18:30 的 4 个明确要求

| # | 要求 | 现状对照 |
|---|---|---|
| **1** | 看见 3 个 bridge 状态（独立终端 OR 主终端反馈） | ❌ 现在是孤儿 bridge，没显示在主终端 |
| **2** | boot = `while 1` 死循环，只在录制结束 + 收尾工作完成后才退 | ❌ 现在 polling break 后接 cam/recorder，结构就是一次性的 |
| **3** | 录制画面可见、按 Enter 才开始、overlay 实时刷新 | ⚠️ 代码已实现（plan.py / --start-on-key / overlay），但**从未实际跑过**——boot 没跑到 cam+recorder 启动就死了 |
| **4** | 我之前幻觉严重，要梳理现状、必要时写 handoff | ✅ 本文件就是 handoff；先停止动键盘 |

---

## 7. 必须问用户的 4 个关键问题（动手前）

### Q1：tmux/screen 多终端方案？
- A. 你能开 3-4 个独立终端（每个一个 bridge），主终端跑 boot 显示 cam 画面
- B. 你希望全部在 1 个终端聚合（boot 用 `tee` 把 bridge stderr 实时回显）
- C. 都不行，先解决"主进程死"再说

### Q2：cam 预览窗口怎么显示？
- A. 你能 ssh + X11 forward / 本地有 display → `cv2.imshow` 直接弹窗
- B. 用 Xvfb 虚拟 display + ffmpeg 转 rtmp stream → 你用 VLC 看
- C. 暂时不要 cam 预览，cam 只负责 mp4 + MQTT publish（看不到画面但能录制）
- D. 其他

### Q3：plan 还要 13 段那么复杂？
- A. 维持 D1 plan（13 段 580s），照 [dev_doc/6 §4](../6-s01-r1-13min-recording-design-2026-07-11.md)
- B. MVP 简化：empty 30s + 站立 60s + 坐下 30s + 仰卧 30s = 150s，先把流程跑通再扩
- C. 完全跳过 plan，先做 empty 60s 验证"录制 + h5 + mp4 三件套"能产出

### Q4：boot 主进程死因要不要继续 debug？
- A. 继续按假设 #1 debug（加 `set -x`，修 `[ ... ] && break` 为 `if [ ... ]; then break; fi`）
- B. 不 debug 了，按你 4 个要求**直接重写 boot**（while 1 死循环 + 实时反馈 + 见画面）
- C. 先把现在的孤儿 bridge 杀掉 + 重置环境，再做任何事

---

## 8. 下一步推荐路径

如果用户回答 Q1=A 或 B + Q2=A 或 C + Q3=任一 + Q4=B（重写），推荐这样：

1. **先杀掉 3 个孤儿 bridge**（`pkill -9 -f bridge.py`）
2. **写 dev_doc/10-boot-v2-design-2026-07-11.md** —— 按用户 4 个要求重设计 boot
3. **写 dev_doc/11-boot-v2-impl-2026-07-11.md** —— 实施计划
4. **执行**（按用户决定 inline / subagent）

如果用户选 Q4=A（继续 debug 假设 #1）：
1. 加 `set -x` 到 boot_recording.sh 顶部重跑
2. 看 trace 是不是 `[ "$ready" -eq 3 ] && break` 退出码触发的
3. 修 `&& break` 为 `if/then/break`
4. 单 commit

---

## 9. CLAUDE.md 决策可追溯要点（按 §3.3）

本会话涉及的所有决策：

| 决策 | 文档位置 |
|---|---|
| D1 plan（3 站 × 4 朝 × 3 组 + supine）| dev_doc/6 §4 |
| boot 单脚本 vs 5 终端 | dev_doc/6 §3（已废弃：用户新要求是多终端 OR 主终端聚合）|
| --start-on-key + --plan + overlay | dev_doc/7 Task 1 |
| PYTHONUNBUFFERED=1 | commit 9323763（**未解决真正问题**）|
| polling 实时 frames 显示 | commit b7a40a5（**未解决真正问题**）|

**未决策**（等用户 Q1-Q4 答完）：
- boot 终端聚合方式
- cam 预览方式
- plan 复杂度
- 是否 debug 还是重写

---

## 10. 给下一个 AI / 下一轮会话的提醒

**不要重蹈我的覆辙**：
1. ❌ 不要根据"症状"猜根因再 patch —— 先看 rawlog/long 实测数据
2. ❌ 不要假设 `set -e` 在某种语法下不触发 —— bash 文档很微妙，要测
3. ❌ 不要承诺"重跑就好" —— 用户已经重跑 3 次都没好
4. ✅ 先写 handoff 或在 dev_doc 写清楚现状，再问用户关键问题
5. ✅ 改代码前必须能答"为什么改这个、改完预期怎样"
6. ✅ 任何"应该能 work"的假设，要用 5 秒 dry-run 验证，而不是直接 commit

**已验证有效的工具**：
- `tail -5 logs/boot-*/rx0.log` 看 frames 涨势
- `ls -lh logs/*.rawlog` 看真实 CSI 数据量
- `pgrep -af bridge.py` 看孤儿
- `kill -9 <pid>` 清孤儿

**未验证**：
- cam_capture 的 overlay 渲染（Python 代码改了但 cam 从未实际跑过）
- recorder 的 --plan 段切换日志（同样从未跑过）
- §5 5 步验收脚本（写好了但没数据可验）

---

**最后更新**：2026-07-11 18:35
**维护者**：Claude
**依据**：用户 2026-07-11 18:30 反馈"幻觉严重、必要时写 handoff"