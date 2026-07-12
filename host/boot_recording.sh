#!/usr/bin/env bash
# boot_recording.sh — orchestrate 5 processes for one csi-pose recording.
# Usage:
#   ./host/boot_recording.sh               [default NORM 580s, 13 segments -> data/+logs/]
#   ./host/boot_recording.sh norm s01-r1   [explicit NORM]
#   ./host/boot_recording.sh test s01-r1   [TEST 60s, 4 segments  -> data/test/+logs/test/]
#
# The two modes write to completely separate directories (data/test/ vs data/) so a
# smoke TEST run never pollutes the NORM training set. See dev_doc/10 for switching
# rules and independence verification.
set -euo pipefail

# Project root = parent of where this script lives (host/ -> project root)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"   # ensure all relative paths work regardless of caller's cwd

# ─── CLI parsing: MODE [SESSION] ──────────────────────────────────────
# Strict: only `test` or `norm` may be the first arg (or empty for default norm).
# Usage: ./host/boot_recording.sh [test|norm] [SESSION]
case "${1:-}" in
    ""|norm|NORM)
        MODE="norm"
        SESSION="${2:-s01-r1}"
        ;;
    test|TEST)
        MODE="test"
        SESSION="${2:-s01-r1}"
        ;;
    *)
        echo "Usage: $0 [test|norm] [SESSION]"
        echo "  default (no args):  NORM 580s 13-segment plan, output -> data/ + logs/"
        echo "  test [SESSION]:     TEST 60s 4-segment plan,    output -> data/test/ + logs/test/"
        echo "  norm  [SESSION]:    NORM (same as default)"
        exit 1
        ;;
esac

PYTHON="/home/ruo/anaconda3/envs/dac_dev/bin/python"
export PYTHONUNBUFFERED=1       # bridge.py print buffered -> boot script grep永远看不到
TS="$(date +%Y%m%d-%H%M%S)"
LOGDIR="logs/boot-${SESSION}-${MODE}-${TS}"

# ─── MODE-specific config (DURATION + plan + output dirs) ────────────
# test: 70s transition-validation run, 2 actions + 1 transition (auto), isolated subdirs
#   effective_plan = stand(30) + transition(10) + squat(30) = 70s
#   设计目的: 验证 transition 特性本身, 不需要 norm 的 12 段复杂度
# norm: 580s 13-segment D1 plan from dev_doc/6 §4, training dirs
#   effective_plan = 580 + 11×10 = 690s (用户传 --duration 580, 依赖 §8 auto 兜底)
#   注意: norm 的 --duration 仍传 580, 让 cam/recorder 各自用 effective_plan 总和兜底
case "$MODE" in
    test)
        DURATION=60
        PLAN="1:stand:30,2:squat:30"
        OUT_DIR="data/test"
        RAW_DIR="logs/test"
        ;;
    norm)
        DURATION=580
        PLAN="1:empty_in:60,2:pos1_set1:40,3:pos2_set1:40,4:pos3_set1:40,5:pos1_set2:40,6:pos2_set2:40,7:pos3_set2:40,8:pos1_set3:40,9:pos2_set3:40,10:pos3_set3:40,11:sit:40,12:lie_supine:60,13:empty_out:60"
        OUT_DIR="data"
        RAW_DIR="logs"
        ;;
esac

mkdir -p "$LOGDIR" "$OUT_DIR" "$RAW_DIR"
# Cleanup any stale gate sentinel from a previous aborted run (per-mode)
rm -f "${OUT_DIR}/.${SESSION}.gate"
echo "=== boot (mode=$MODE session=$SESSION ts=$TS) $(date) ==="
echo "    duration=${DURATION}s  plan=\"$(echo "$PLAN" | cut -c1-60)...\""
echo "    out=${OUT_DIR}/  raw=${RAW_DIR}/  log=${LOGDIR}/"

# ① 预检
command -v mosquitto >/dev/null || { echo "❌ mosquitto not installed"; exit 1; }
pgrep mosquitto >/dev/null || mosquitto -d -p 1883
for p in 0 1 2; do [ -e "/dev/ttyACM$p" ] || { echo "❌ /dev/ttyACM$p missing"; exit 1; }; done
[ -e /dev/video0 ] || { echo "❌ /dev/video0 missing"; exit 1; }
echo "✓ preflight OK (mode=$MODE session=$SESSION)"

# ② 后台启 3 bridge (tee 聚合: stderr → 终端 + live.log; polling 从 live.log 按 rx-id grep)
# bridge --raw-dir 走 $RAW_DIR，确保 test/norm 各自落子目录
# --log-ts 传入 boot 的 TS，避免 bridge 启动晚 1 秒导致 rawlog 文件名与 ${TS} glob 失配
BRIDGE_PIDS=()
: > "$LOGDIR/live.log"        # truncate, 由 tee -a append
for rx in 0 1 2; do
    "$PYTHON" host/bridge/bridge.py --port "/dev/ttyACM$rx" --rx-id "$rx" \
        --raw-dir "$RAW_DIR" --log-ts "$TS" --status-period 1.0 \
        2>&1 | tee -a "$LOGDIR/live.log" >/dev/null &
    BRIDGE_PIDS+=($!)
done

# ③ 轮询等所有 bridge frames > 280
trap 'kill ${BRIDGE_PIDS[@]:-} ${CAM_PID:-} ${REC_PID:-} 2>/dev/null || true; exit 1' INT TERM
echo "Waiting for 3 bridges (frames > 280)..."
# 注：pipefail + set -e 下, pipeline 返回非零会让 $(...) 触发 set -e 退出整个脚本
# 兜底：tail -1 之后加 || echo 0，强制返回 0
get_frames() {
    grep "\[rx$1\]" "$LOGDIR/live.log" 2>/dev/null | grep -oP '"frames":\s*\K\d+' | tail -1 || echo 0
}
while true; do
    ready=0
    f0=$(get_frames 0); f0="${f0:-0}"
    echo "[poll] f0='$f0'"
    if [ "$f0" -gt 280 ]; then ready=$((ready + 1)); fi
    f1=$(get_frames 1); f1="${f1:-0}"
    echo "[poll] f1='$f1'"
    if [ "$f1" -gt 280 ]; then ready=$((ready + 1)); fi
    f2=$(get_frames 2); f2="${f2:-0}"
    echo "[poll] f2='$f2'"
    if [ "$f2" -gt 280 ]; then ready=$((ready + 1)); fi
    echo "  [$(date +%H:%M:%S)] ready=$ready/3  frames: rx0=$f0 rx1=$f1 rx2=$f2"
    if [ "$ready" -eq 3 ]; then break; fi
    sleep 2
done
echo "✓ 3 bridges ready"

# ④ 后台启 cam + recorder (用户按 Enter 开始)
# cam/recorder --out 走 $OUT_DIR，确保 test/norm 各自落子目录
"$PYTHON" host/capture/cam_capture.py \
    --camera 0 --backend any --out "$OUT_DIR" --session "$SESSION" --duration "$DURATION" \
    --width 640 --height 360 --fps 30 \
    --plan "$PLAN" --start-on-key --overlay --status-period 1.0 \
    > "$LOGDIR/cam.log" 2>&1 &
CAM_PID=$!

"$PYTHON" host/recorder/recorder.py \
    --out "$OUT_DIR" --session "$SESSION" --duration "$DURATION" \
    --plan "$PLAN" --start-on-key --status-period 1.0 \
    > "$LOGDIR/recorder.log" 2>&1 &
REC_PID=$!

# ⑤ 等自然退出
# || CAM_RC=$? 双兜底：(a) 捕获子进程退出码 (b) 防止 set -e 触发主进程退出
CAM_RC=0
wait "$CAM_PID" || CAM_RC=$?
REC_RC=0
wait "$REC_PID" || REC_RC=$?

# ⑥ 杀 bridges
kill "${BRIDGE_PIDS[@]}" 2>/dev/null || true
wait "${BRIDGE_PIDS[@]}" 2>/dev/null || true

# ⑦ 打印产物 (按 MODE 路径各自 glob，绝不交叉)
echo ""
echo "=== Recording complete (mode=$MODE) ==="
echo "Cam exit: $CAM_RC | Recorder exit: $REC_RC"
shopt -s nullglob
H5=("${OUT_DIR}"/${SESSION}-*.h5)
MP4=("${OUT_DIR}"/${SESSION}-*.mp4)
RAW=("${RAW_DIR}"/rx*-${TS}.rawlog)
if [ "${#H5[@]}" -eq 0 ] || [ "${#MP4[@]}" -eq 0 ] || [ "${#RAW[@]}" -lt 3 ]; then
    echo "❌ Missing in $OUT_DIR/: h5=${#H5[@]} mp4=${#MP4[@]} | in $RAW_DIR/: raw=${#RAW[@]}"
    ls -lh "${H5[@]}" "${MP4[@]}" "${RAW[@]}" 2>/dev/null || true
    exit 1
fi
ls -lh "${H5[0]}" "${MP4[0]}" "${RAW[@]}"
echo "Boot log: $LOGDIR/"
