#!/usr/bin/env bash
# snapshot_usb.sh — Capture USB topology + dmesg history + power state.
# Run BEFORE and AFTER a recording to find RX USB disconnects / power
# suspend events that might explain CSI loss gaps.
#
# Usage:
#   ./host/tools/snapshot_usb.sh              # auto-named log
#   ./host/tools/snapshot_usb.sh pre          # prefix pre-<ts>.log
#   ./host/tools/snapshot_usb.sh post         # prefix post-<ts>.log
#
# Output: writes to logs/usb-snap-<label>-<ts>.log
# Then diff pre/post to see if anything changed (USB resets, disconnects, etc.)
set -euo pipefail

LABEL="${1:-snap}"
TS="$(date +%Y%m%d-%H%M%S)"
OUT="logs/usb-snap-${LABEL}-${TS}.log"
mkdir -p "$(dirname "$OUT")"

{
    echo "=== USB snapshot @ $(date) ($LABEL) ==="
    echo
    echo "--- /sys/bus/usb/devices tree (which ttyACM is on which port) ---"
    for d in /sys/bus/usb/devices/*/; do
        # Skip root hubs / non-tty devices
        if [ -d "$d/tty" ] || [ -n "$(ls "$d"/tty 2>/dev/null)" ] || [ -f "$d/product" ]; then
            devname=$(basename "$d")
            prod="$(cat "$d/product" 2>/dev/null || true)"
            manuf="$(cat "$d/manufacturer" 2>/dev/null || true)"
            speed="$(cat "$d/speed" 2>/dev/null || true)"
            pwr="$(cat "$d/power/control" 2>/dev/null || true)"
            ttys="$(ls "$d"/tty 2>/dev/null | xargs 2>/dev/null || true)"
            echo "  $devname  ${prod:-(no product)}  ${manuf:+by $manuf}  speed=${speed}bps  power=$pwr  ttys=$ttys"
        fi
    done
    echo
    echo "--- lsusb (full topology) ---"
    lsusb -t 2>&1 || echo "  (lsusb missing — install usbutils)"
    echo
    echo "--- /dev/ttyACM0,1,2 open? ---"
    for p in 0 1 2; do
        if [ -e "/dev/ttyACM$p" ]; then
            echo "  /dev/ttyACM$p: present"
            stat -c "    %n  size=%s  mtime=%y" "/dev/ttyACM$p" 2>/dev/null
        else
            echo "  /dev/ttyACM$p: MISSING"
        fi
    done
    echo
    echo "--- dmesg recent USB/tty events (last 50 lines) ---"
    dmesg --color=never 2>/dev/null | grep -iE 'usb|tty|cdc_acm|xhci|hub' | tail -n 50 || echo "  (dmesg unreadable)"
    echo
    echo "--- count of distinct USB events since boot ---"
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
} > "$OUT" 2>&1

echo "Wrote: $OUT"
echo "  diff with another snapshot to see what changed:"
echo "    diff <(cat logs/usb-snap-pre-*.log) <(cat logs/usb-snap-post-*.log) | head -40"
