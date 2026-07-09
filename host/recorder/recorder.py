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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mqtt-host", default="127.0.0.1")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--out", default="sessions", help="Session directory")
    ap.add_argument("--session", required=True, help="Session label (filename)")
    ap.add_argument("--duration", type=float, default=None, help="Seconds — omit for Ctrl-C")
    ap.add_argument("--status-period", type=float, default=5.0)
    args = ap.parse_args()

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
    core = RecorderCore(writer, on_event=lambda k, v: print(f"[rec] {k}: {v}", flush=True))

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
    print(f"[rec] Recording: {path}", flush=True)

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
            if now - last >= args.status_period:
                last = now
                writer.flush()                      # Periodic flush (spec error handling)
                print(f"[rec] {core.status()}", flush=True)
            if args.duration is not None and now - t0 >= args.duration:
                break    # is not None: --duration 0 also exits immediately (falsy guard would be infinite record bug)
    except KeyboardInterrupt:
        print("\n[rec] stopping", flush=True)
    finally:
        client.loop_stop()
        writer.set_meta("recorder_status", str(core.status()))
        writer.close()
        print(f"[rec] Ended: frames={core.frames} crc_drops={core.crc_drops} -> {path}",
              flush=True)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
