# 15 — snapshot_usb.sh 静默退出修复（dmesg 权限）

**日期**：2026-07-12
**状态**：✅ 已修复并验证
**影响范围**：[host/tools/snapshot_usb.sh:55](host/tools/snapshot_usb.sh#L55)（commit `ff9e99b` 引入）
**触发条件**：以非 root 用户运行 `snapshot_usb.sh pre|post`

---

## 1. 症状

```bash
$ ./host/tools/snapshot_usb.sh pre
$ # 立即返回，无任何 stdout/stderr 输出
$ echo $?
1
```

`logs/usb-snap-pre-*.log` 大小卡在 3579 字节，提前在 "count of distinct USB events" 一节末尾断裂。

---

## 2. 根因

[snapshot_usb.sh:55](host/tools/snapshot_usb.sh#L55) 的 pipeline：

```bash
n=$(dmesg --color=never 2>/dev/null | grep -ic "$kind" | head -1)
```

3 个失败条件叠加：

| 条件 | 现象 |
|---|---|
| `dmesg` 需要 `CAP_SYSLOG` | 用户 `ruo` 没有：`dmesg: 读取内核缓冲区失败: 不允许的操作`，退出码 1 |
| `set -o pipefail` | dmesg 失败 → 整个 pipeline 返回 1 |
| `set -e` + `n=$(...)` | 命令替换的非零退出触发脚本静默退出 |

**为什么 bash -x 只看到 7 行就退出？** 因为 brace block 的 `} > "$OUT" 2>&1` 同时吞掉了 bash xtrace 的 stderr。bash 实际跑到 line 535 之后才挂，但输出文件看起来死在 mkdir。

**对比**：line 51 的另一个 dmesg pipeline 有 `|| echo " (dmesg unreadable)"` 兜底，line 55 漏了。

---

## 3. 修复

[snapshot_usb.sh:54-62](host/tools/snapshot_usb.sh#L54-L62) — 三级 fallback，dmesg → journalctl -k → kern.log → 0：

```bash
for kind in 'disconnect' 'reconnect' 'reset' 'suspend' 'resume'; do
    # dmesg needs kmsg CAP_SYSLOG; on systems without it (e.g. non-root user
    # without CAP_SYSLOG) dmesg returns 1, which +pipefail +set -e would
    # silently kill the whole script. Fall back to journalctl -k (works
    # without root when user is in systemd-journal) or /var/log/kern.log
    # (adm group), and finally to 0 if nothing readable.
    n=$(dmesg --color=never 2>/dev/null | grep -ic "$kind" | head -1) \
        || n=$(journalctl -k --no-pager 2>/dev/null | grep -ic "$kind" | head -1) \
        || n=$(test -r /var/log/kern.log && grep -ic "$kind" /var/log/kern.log | head -1) \
        || n=0
    echo "  $kind: $n events"
done
```

`本机 ruo 是 adm 组成员 → /var/log/kern.log 可读 → fallback 命中`。

---

## 4. 验证

```bash
$ ./host/tools/snapshot_usb.sh pre
Wrote: logs/usb-snap-pre-20260712-092058.log
  diff with another snapshot to see what changed:
    diff <(cat logs/usb-snap-pre-*.log) <(cat logs/usb-snap-post-*.log) | head -40
$ echo $?
0
```

`logs/usb-snap-pre-20260712-092058.log` 末尾：

```
--- count of distinct USB events since boot ---
  disconnect: 6 events
  reconnect: 0 events
  reset: 1 events
  suspend: 0 events
  resume: 0 events
```

`post` 也工作正常，handoff 文档 §4.2 的 diff 命令能输出实际 USB 断开/重连事件。

---

## 5. 给后续的提示

- **凡是用 `set -euo pipefail` + 命令替换 `$()` 的脚本**，任何命令替换失败都会让脚本静默退出。设计时要么兜底（`|| ...`），要么显式 `|| true`。
- **诊断这类"静默死"的脚本**，在 brace block 外的位置加 `set -x` 看不到内部 trace；要么把 `} > "$OUT" 2>&1` 临时改成不重定向，要么直接 `strace -f -e trace=execve,exit_group`。
- **本机排查 USB 链路**：优先 `tail -f /var/log/kern.log`（adm 组成员能读），不需要 sudo。

---

**维护者**：Claude
**依据**：handoff §4.2 工具 + 本次现场复现