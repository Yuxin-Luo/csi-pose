# 19 — Known Issues (Deferred, Not Blocking E2E)

**日期**：2026-07-12
**状态**：⏸️ 已记录，未修复
**触发**：用户 2026-07-12 调整项目目标为"先跑通全流程，优化项延后"

---

## 0. 上下文

dev_doc/17/18 实现了 transition 特性（每对相邻 action 段间插 10s 缓冲 + h5 segment 标记 + magenta overlay）。Tasks 1-7 单测通过 16/16，但端到端 test mode 暴露了若干**与 transition 本身无关**的环境/UX 问题。

**本策略**：保留扩展代码（dev_doc/17/18 不删），加 `--no-transition` 行为开关（让 cam/recorder 走原作者最简路径），跑通端到端为先。这些问题记到本文，未来优化。

---

## 1. boot test mode `--duration` 与 effective_plan 不一致

**现象**：`host/boot_recording.sh:53` test mode 传 `--duration 60`，但 effective_plan（test plan `1:stand:30,2:squat:30`）总和是 **70s**（30 + 10 transition + 30）。

**影响**：cam 和 recorder 都在 60s 时退出，effective_plan 70s 中最后 10s（squat 末尾）**永远录不到**。transition 段也只是 cam 自己 tick 时可见，recorder 完全没跑到 transition 段就退了。

**当前绕过**：改用 `--no-transition` 旗标后 effective_plan 等于原始 plan（60s），与 boot 的 `--duration 60` 一致。

**未来修复**：dev_doc/18 §8 spec 的"auto 兜底"——cam_capture / recorder 自算 `sum(effective_plan)`，忽略 boot 的 `--duration`（仅作 ETA 日志）。

---

## 2. cam fps 物理上限 ~15fps（calibrate=20.64）

**现象**：cam_capture 主循环实测 fps_live ~15，cam_capture 自校准 `calibrated fps: real=20.85`。即使 `--no-skeleton` 也只有 ~15fps。

**复现**：
- 纯净 cam pipeline（直接 cap.read + mp4v write）：30 fps ✅
- cam_capture 主循环（含 calibrate + putText + imshow + status print）：**20.85 → 15 fps**

**根因（已确认）**：
- `calibrate = 20.85` 是 cam 自测 `30 × cap.grab()` 的实测值，**是摄像头 + USB bus 物理上限**
- fps_live 是 wall-clock 算的（含 writer.encode + putText + imshow + 每 1s status print）
- 跟 transition / skeleton / MQTT publish 都**无关**

**影响**：cam 录出来的 mp4 时长 < wall-clock duration（实测 cam 跑 6s wall-clock → mp4 4.5s @ 20.5fps）。这对训练/推理**没影响**——label 按 cam 帧索引，不按 wall-clock。

**未来优化**：
- 选项 A：换 USB3 摄像头 / 改 YUY2 → MJPG
- 选项 B：接受 15fps，teacher.py label 路径不变
- 选项 C：把 CSI 130Hz 跟 cam 15fps 解耦（已实现，cli_args `--duration` 各自走）

**当前决定**：接受 15fps，不动。

---

## 3. Ctrl-C 杀掉 cam 导致 mp4 moov atom 缺失

**现象**：用户 Ctrl-C 强杀 cam_capture 进程 → `[cam] Ended:` 行不打印 → finally 块没跑 → `writer.release()` 没调 → mp4 没有 moov atom → ffprobe 报 `moov atom not found`。

**当前绕过**：用优雅退出（按 X 关窗口或 cam 自身 duration 到期）。

**未来修复**（如必要）：
- 选项 A：cam_capture 加 SIGINT handler 调 finally（保留 cv2.waitKey 主循环响应）
- 选项 B：用 `atexit` 注册 writer.release
- 选项 C：h5 录制也类似需要加固（不过 boot 杀 recorder 时也走 graceful，h5 完整）

**当前决定**：用户操作问题，不修。

---

## 4. dev_doc/18 spec §6 / §10 部分数值偏差

**现象**：dev_doc/18 spec 几处对 test/norm 模式的预期时长、段数在历史 review 中已修正（13 action / 25 effective / 700s 是最终正确值），但 spec 表格里的"DURATION=580" / "test=60s" 等描述仍存在弱一致性问题（跟 effective_plan 总和不对齐）。

**当前绕过**：boot 改用 `--no-transition` 后这些数字重新一致。

**未来修复**：dev_doc/18 spec 表格里改"auto = sum(effective_plan)"，删除硬编码 580 / 60。

---

## 5. CSI 同步录制仍然依赖 USB 串口

dev_doc/11/13 已记录（USB disconnect 引起 loss spike），不在本文范围。

---

## 6. 给后续 agent 的提示

- **不要回到 `<2cff6d2` baseline**：现有 11 commit + dev_doc/17/18 完整保留
- **`--no-transition` 是临时简化旗标**：不是废弃 transition 特性
- **本文件不是 bug report**：是"已知现状"备忘，未来按需重启

---

**维护者**：Claude
**依据**：用户 2026-07-12 反馈"先跑通全流程，变数越少越好"