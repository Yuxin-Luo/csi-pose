#!/usr/bin/env python3
"""Live recorder — MQTT (csi/rx*, cam/meta) subscription -> session HDF5 (native windows).

  python recorder.py --out ..\\sessions --session s01-r1 [--duration 600]

The original is still the bridge rawlog -- even if the recorder dies, can rebuild with rawlog_to_hdf5.
Required packages: paho-mqtt, numpy, h5py (see requirements.txt).
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # host/
from csi_pipe.mqtt_recorder import RecorderCore, wire_client  # noqa: E402
from csi_pipe.store import SessionWriter  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "capture"))  # host/capture/
from plan import parse_plan, expand_plan  # noqa: E402
import plan as _plan_module  # for --no-transition runtime override (see main())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mqtt-host", default="127.0.0.1")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--out", default="sessions", help="Session directory")
    ap.add_argument("--session", required=True, help="Session label (filename)")
    ap.add_argument("--duration", type=float, default=None, help="Seconds — omit for Ctrl-C")
    ap.add_argument("--status-period", type=float, default=5.0)
    ap.add_argument("--start-on-key", action="store_true", help="Wait for Enter before recording")
    ap.add_argument("--plan", default=None, help='Plan string (stderr log + HDF5 meta only)')
    ap.add_argument("--no-transition", action="store_true",
                    help="Disable plan.TRANSITION_S_DEFAULT (effective_plan == plan_list). "
                         "Use for simplest 2-action recordings (dev_doc/19).")
    args = ap.parse_args()
    # --no-transition: monkey-patch module constant so expand_plan() degrades to original plan.
    # Keeps the transition feature code intact for future re-enable; just bypasses at runtime.
    if args.no_transition:
        _plan_module.TRANSITION_S_DEFAULT = 0
        print("[rec] --no-transition: TRANSITION_S_DEFAULT=0 (effective_plan == plan_list)", flush=True)
    plan_list = parse_plan(args.plan) if args.plan else []
    effective_plan = expand_plan(plan_list) if plan_list else []

    import paho.mqtt.client as mqtt

    try:  # paho 2.x
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except (AttributeError, TypeError):  # paho 1.x
        client = mqtt.Client()
    client.enable_logger()                  # Make connection failures/reconnects visible on stderr
    client.connect(args.mqtt_host, args.mqtt_port)   # Before file creation -- no empty .h5 on failure

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{args.session}-{time.strftime('%Y%m%d-%H%M%S')}.h5"
    writer = SessionWriter(path, meta={"session": args.session})
    core = RecorderCore(writer, on_event=lambda k, v: print(f"[rec] {k}: {v}", flush=True),
                        effective_plan=effective_plan)

    # paho swallows callback exceptions -- if handle() fails, silent no-data session results.
    # Only the first exception is detailed, then set an exit flag so the main loop terminates abnormally.
    _error_flag = [None]   # [exception] or [None] -- list for thread-safe sharing

    def _on_message(client, userdata, msg):
        if _error_flag[0] is not None:
            return                          # Already ending -- skip additional processing
        try:
            core.handle(msg.topic, msg.payload, time.time_ns())
        except Exception as exc:           # noqa: BLE001
            # Only first exception is detailed -- stderr before paho swallows it
            print("\n[rec] Fatal error -- handle() exception (HDF5 write failure etc.):", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            _error_flag[0] = exc

    # wire_client is called after connect(), before loop_start().
    # CONNACK is processed in loop_start()'s network thread,
    # so at that point on_connect -> subscribe fires -- this order must be maintained to avoid
    # missing subscriptions on first connection.
    wire_client(client, _on_message, log=lambda msg: print(msg, flush=True))
    client.loop_start()
    if args.start_on_key:
        # &-launched subprocess stdin may not be a TTY → input() raises EOFError.
        # Try stdin if it's a real TTY; otherwise wait for sentinel file
        # (boot_recording.sh writes $SESSION.gate before the cam key is needed).
        gate_flag = Path(args.out) / f".{args.session}.gate"
        if sys.stdin.isatty():
            print("[rec] Press Enter to start recording (recorder stdin)...", flush=True)
            try:
                sys.stdin.readline()
            except (EOFError, KeyboardInterrupt):
                print(f"[rec] stdin closed, waiting for sentinel {gate_flag}...",
                      flush=True)
                while not gate_flag.exists():
                    time.sleep(0.2)
                gate_flag.unlink(missing_ok=True)
        else:
            print(f"[rec] stdin not a TTY, waiting for sentinel {gate_flag}...",
                  flush=True)
            while not gate_flag.exists():
                time.sleep(0.2)
            gate_flag.unlink(missing_ok=True)
    print(f"[rec] Recording: {path}", flush=True)

    t0_wall_ns = time.time_ns()       # dev_doc/17 §4.5: 与 cam_capture 共享 wall-clock
    core.set_recording_start(t0_wall_ns)
    t0 = time.monotonic()
    last = t0
    exit_code = 0
    try:
        while True:
            time.sleep(0.2)
            # handle() exception -> immediate abnormal termination
            if _error_flag[0] is not None:
                print("[rec] handle() exception detected -- abnormal termination", file=sys.stderr, flush=True)
                exit_code = 1
                break
            now = time.monotonic()
            if effective_plan:
                elapsed = now - t0
                new_seg_idx = -1
                cum = 0.0
                for i, seg in enumerate(effective_plan):
                    cum += seg.duration_s
                    if elapsed < cum:
                        new_seg_idx = i
                        break
                else:
                    new_seg_idx = len(effective_plan) - 1
                if not hasattr(main, "_last_seg") or main._last_seg != new_seg_idx:
                    # 段切换: 先关闭 PREV segment 的范围
                    if hasattr(main, "_last_seg") and hasattr(main, "_seg_start_t_ns"):
                        prev = effective_plan[main._last_seg]
                        writer.update_segment(
                            start_t_ns=main._seg_start_t_ns,
                            end_t_ns=t0_wall_ns + int((cum - seg.duration_s) * 1e9),
                            name=prev.name,
                            state=prev.state,
                        )
                    # 开启 NEW segment 的范围
                    cur = effective_plan[new_seg_idx]
                    main._seg_start_t_ns = t0_wall_ns + int((cum - seg.duration_s) * 1e9)
                    main._last_seg = new_seg_idx
                    print(f"[rec] segment {new_seg_idx + 1}/{len(effective_plan)} -> "
                          f"{cur.name} ({cur.state})", flush=True)
            if now - last >= args.status_period:
                last = now
                writer.flush()                      # Periodic flush (spec error handling)
                print(f"[rec] {core.status()}", flush=True)
            if args.duration is not None and now - t0 >= args.duration:
                break    # is not None: --duration 0 also exits immediately (falsy guard would be infinite record bug)
    except KeyboardInterrupt:
        print("\n[rec] stopping", flush=True)
    finally:
        # dev_doc/17 §4.3: 关闭最后一段的范围
        if effective_plan and hasattr(main, "_last_seg") and hasattr(main, "_seg_start_t_ns"):
            last_seg = effective_plan[main._last_seg]
            writer.update_segment(
                start_t_ns=main._seg_start_t_ns,
                end_t_ns=time.time_ns(),
                name=last_seg.name,
                state=last_seg.state,
            )
        if args.plan:
            writer.set_meta("plan", args.plan)
        client.loop_stop()
        writer.set_meta("recorder_status", str(core.status()))
        writer.close()
        print(f"[rec] Ended: frames={core.frames} crc_drops={core.crc_drops} -> {path}",
              flush=True)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
