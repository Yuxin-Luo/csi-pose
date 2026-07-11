#!/usr/bin/env bash
# boot_recording.sh — orchestrate 5 processes for one csi-pose recording.
# Usage: ./host/boot_recording.sh [SESSION_NAME]
set -euo pipefail

SESSION="${1:-s01-r1}"
PYTHON="/home/ruo/anaconda3/envs/dac_dev/bin/python"
TS="$(date +%Y%m%d-%H%M%S)"
LOGDIR="logs/boot-${SESSION}-${TS}"
DURATION=580

PLAN="1:empty_in:60,2:pos1_set1:40,3:pos2_set1:40,4:pos3_set1:40,5:pos1_set2:40,6:pos2_set2:40,7:pos3_set2:40,8:pos1_set3:40,9:pos2_set3:40,10:pos3_set3:40,11:sit:40,12:lie_supine:60,13:empty_out:60"

mkdir -p "$LOGDIR" data

# ① 预检
command -v mosquitto >/dev/null || { echo "❌ mosquitto not installed"; exit 1; }
pgrep mosquitto >/dev/null || mosquitto -d -p 1883
for p in 0 1 2; do [ -e "/dev/ttyACM$p" ] || { echo "❌ /dev/ttyACM$p missing"; exit 1; }; done
[ -e /dev/video0 ] || { echo "❌ /dev/video0 missing"; exit 1; }
echo "✓ preflight OK (session=$SESSION, ts=$TS)"

# ② 后台启 3 bridge
BRIDGE_PIDS=()
for rx in 0 1 2; do
    "$PYTHON" host/bridge/bridge.py --port "/dev/ttyACM$rx" --rx-id "$rx" \
        --raw-dir logs --status-period 1.0 \
        > "$LOGDIR/rx$rx.log" 2>&1 &
    BRIDGE_PIDS+=($!)
done

# ③ 轮询等所有 bridge frames > 280
trap 'kill ${BRIDGE_PIDS[@]:-} 2>/dev/null || true; exit 1' INT TERM
echo "Waiting for 3 bridges (frames > 280)..."
while true; do
    ready=0
    for rx in 0 1 2; do
        f=$(grep -oP '"frames":\s*\K\d+' "$LOGDIR/rx$rx.log" 2>/dev/null | tail -1)
        [ "${f:-0}" -gt 280 ] && ready=$((ready + 1))
    done
    [ "$ready" -eq 3 ] && break
    sleep 2
done
echo "✓ 3 bridges ready"

# ④ 后台启 cam + recorder（用户按 Enter 开始）
"$PYTHON" host/capture/cam_capture.py \
    --camera 0 --backend any --out data --session "$SESSION" --duration "$DURATION" \
    --plan "$PLAN" --start-on-key --overlay --status-period 1.0 \
    > "$LOGDIR/cam.log" 2>&1 &
CAM_PID=$!

"$PYTHON" host/recorder/recorder.py \
    --out data --session "$SESSION" --duration "$DURATION" \
    --plan "$PLAN" --start-on-key --status-period 1.0 \
    > "$LOGDIR/recorder.log" 2>&1 &
REC_PID=$!

# ⑤ 等自然退出
wait "$CAM_PID"; CAM_RC=$?
wait "$REC_PID"; REC_RC=$?

# ⑥ 杀 bridges
kill "${BRIDGE_PIDS[@]}" 2>/dev/null || true
wait "${BRIDGE_PIDS[@]}" 2>/dev/null || true

# ⑦ 打印产物
echo ""
echo "=== Recording complete ==="
echo "Cam exit: $CAM_RC | Recorder exit: $REC_RC"
shopt -s nullglob
H5=(data/${SESSION}-*.h5); MP4=(data/${SESSION}-*.mp4); RAW=(logs/rx*-${TS}.rawlog)
if [ "${#H5[@]}" -eq 0 ] || [ "${#MP4[@]}" -eq 0 ] || [ "${#RAW[@]}" -lt 3 ]; then
    echo "❌ Missing: h5=${#H5[@]} mp4=${#MP4[@]} raw=${#RAW[@]}"
    ls -lh "${H5[@]}" "${MP4[@]}" "${RAW[@]}" 2>/dev/null || true
    exit 1
fi
ls -lh "${H5[0]}" "${MP4[0]}" "${RAW[@]}"
echo "Boot log: $LOGDIR/"
