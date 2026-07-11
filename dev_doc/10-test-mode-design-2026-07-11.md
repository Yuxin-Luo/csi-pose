# Test Mode for boot_recording.sh — 独立 smoke run

**状态**：✅ 完成（2026-07-11）
**目的**：1-min smoke test 不污染 580s 真实训练采集；两个 MODE 互不干扰
**实施**：commit 见末尾

---

## 1. 核心设计原则

**两个 MODE 写完全不同的子目录，凭路径天然隔离**——没有任何代码会同时 touch 这两个目录，绝不互删。

| 模式 | 时长 | plan | h5/mp4 输出 | rawlog 输出 | log 输出 |
|---|---|---|---|---|---|
| `test` | 60 s | 4 段 (empty 15 + walk 25 + lie 10 + empty 10) | `data/test/` | `logs/test/` | `logs/boot-{SESSION}-test-{TS}/` |
| `norm` | 580 s | 13 段 D1 plan | `data/` | `logs/` | `logs/boot-{SESSION}-norm-{TS}/` |

目录命名规则：`MODE` 是 path 的字面前缀——肉眼一眼能区分，永远不会 glob 撞。

---

## 2. 用法

```bash
# 默认 = NORM 580s（兼容新写法）
./host/boot_recording.sh                            # mode=norm session=s01-r1
./host/boot_recording.sh norm                       # 同上
./host/boot_recording.sh norm s02-r1                # mode=norm session=s02-r1

# TEST 60s smoke
./host/boot_recording.sh test                       # mode=test session=s01-r1
./host/boot_recording.sh test s02-smoke             # mode=test session=s02-smoke

# 错误用法：第一个参数不是 test/norm 时显式报错（不再有 back-compat 歧义）
./host/boot_recording.sh s01-r2                     # → exit 1 + usage
```

**没有任何 usage 等同于 `boot_recording.sh SESSION`**——`./boot_recording.sh s01-r2` 现在报 *"Usage: $0 [test|norm] [SESSION]"*。要跑自定义 session 必须显式 `norm`：

```bash
./host/boot_recording.sh norm s01-r2       # OK
./host/boot_recording.sh test s01-smoke-1  # OK
```

---

## 3. TEST plan 设计依据

为什么是这 4 段、不只做 empty 60s？

| 段 | 时长 | 验收什么 |
|---|---|---|
| `1:empty_in:15` | 15s | 启动无帧丢弃 |
| `2:walk:25` | 25s | plan 段切换 + 走姿 mp4 有差异 |
| `3:lie_supine:10` | 10s | 躺姿 + 短段（验证 10s 段切换不死）|
| `4:empty_out:10` | 10s | 段末尾体 + 总时长 = 60s = DURATION |

**总长 60s 严格 = `DURATION=60`**，保证 plan 跑完 == duration 结束。

---

## 4. 独立性验证（如何证明不互删）

为未来 agent / 用户保留这两条 recipe，跑通就 100% 确认两个模式独立：

### 4.1 TEST first, NORM second —— 验证 NORM 不踩 TEST

```bash
# Step A: 跑 TEST
./host/boot_recording.sh test s01-rA
# 等完成，预期 data/test/s01-rA-*.{h5,mp4} 出现
ls data/test/  # 应该有 s01-rA-*.h5 + .mp4

# Step B: 跑 NORM
./host/boot_recording.sh norm s01-rB

# Step C: 确认 TEST 产物**还在**（NORM 没删）
ls data/test/  # 应仍含 s01-rA-*.h5 + .mp4 (没被 NORM 触动)
ls data/       # 应只含 s01-rB-*.h5 + .mp4 (没含 s01-rA-* 因为后者在 data/test/)
```

### 4.2 跑之前清理

```bash
# 清干净两个 MODE 目录
rm -rf data/test/* logs/test/*          # 清 TEST
ls data/s01-*.h5 2>/dev/null && echo "warning: data/ has s01- files"
ls data/test/ 2>/dev/null               # 应为空 (mkdir -p 留下空目录)
ls logs/test/ 2>/dev/null               # 应为空
```

### 4.3 路径失败检测

如果有人未来不小心在 cam/recorder 或 bridge 写错了 `--out` / `--raw-dir`，会立即出错——例如 cam 写 `data/`，产物会出现在 `data/` 而不是 `data/test/`，本 recipe 立刻捕获。

---

## 5. 实施清单

| 文件 | 改动 |
|---|---|
| `host/boot_recording.sh` | + MODE 参数 + case 分支决定 DURATION/PLAN/OUT_DIR/RAW_DIR/LOGDIR；旧 session-only 写法改 strict：未识别首参报 usage |
| `dev_doc/10-test-mode-design-2026-07-11.md` | 本文件 |

**cam_capture.py / recorder.py / bridge.py 都没改**——它们已经支持 `--out` / `--raw-dir` 参数化，只要 boot.sh 给对路径就行。

**为什么不需要改 cam_capture 内部**：cam/recorder 是“无知的执行者”，它们不知道 MODE 概念。**Mode 是 boot.sh 的策略决策**——谁跑多长、产物落哪里。

---

## 6. 切换决策树

```
你有 580s 训练数据需求？ 
├─ yes → ./host/boot_recording.sh norm [SESSION]
└─ no  → 你现在跑什么？
    ├─ test  → ./host/boot_recording.sh test [SESSION]
    └─ 还没录 → 先 test，再判断升 norm
```

**MVP 路径建议**（用户当前 2026-07-11 处境）：
1. `./host/boot_recording.sh test s01-smoke` —— 60s 全链路 smoke
2. 验收 §5 三件套产物存在 + 大小合理
3. 都 OK → `./host/boot_recording.sh norm s01-r1` —— 580s 真实训练数据

---

## 7. 相关决策追溯

| 决策 | 来源 |
|---|---|
| TEST 时长 = 60s | 用户 2026-07-11 22:00 反馈：完整 580s 太费时，先小测 |
| 隔离子目录 vs 同目录加前缀 | 用户原话"俩方案互相独立，使用不同采集文件避免互相干扰"——子目录硬隔离 |
| 严格 CLI 不兼容 s01-r2 only | 避免 `./boot_recording.sh s01-r2` 隐式 norm 跟用户意图冲突，强制显式 |
| TEST plan 含 walk + lie 段 | 仅 empty 60s 验不出段切换，加 walk/lie 才能确认 plan 写入正常 |
| DURATION=60 == plan 总长=60 | 否则 duration 先断 = plan 未跑完 = 段切换日志不全 |

---

## 8. 不做的事（out of scope）

- ❌ **不做并发录制多个 session** —— 现在的设计假设同时只有一次 boot
- ❌ **不做 mode 之间互相覆盖** —— 这是 anti-feature，违反用户"互相独立"要求
- ❌ **不做 mode 自动探测** —— 当前 SESSION+TS 决定是否跑，但 MODE 必须显式

---

**依据**：父项目 CLAUDE.md §8 dev_doc 规范 + 用户 2026-07-11 22:00 反馈
**维护者**：Claude
**commit 范围**：仅 `host/boot_recording.sh` + `dev_doc/10-*.md`
