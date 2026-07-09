#!/usr/bin/env python3
"""Webcam capture -- mp4 local recording + MQTT cam/meta {frame_idx, t_ns} publication.

  python cam_capture.py --out ..\\sessions --session s01-r1 [--duration 600] [--no-mqtt]

t_ns is host clock right after grab (same principle as bridge) -- offset from exposure time
is measured by LED alignment verification.
Auto exposure/focus/WB attempts to disable and logs request vs actual (Section 3.3 -- some webcams ignore set).
Required packages: opencv-python, msgpack, paho-mqtt.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # host/
from csi_host.cam_core import CamCore  # noqa: E402


# ---------------------------------------------------------------------------
# MQTT sink -- same pattern as bridge.py (NullSink / MqttSink)
# ---------------------------------------------------------------------------

class NullSink:
    def publish(self, topic, payload):
        pass

    def close(self):
        pass


class MqttSink:
    def __init__(self, host, port):
        import paho.mqtt.client as mqtt
        try:  # paho 2.x
            self._c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except (AttributeError, TypeError):  # paho 1.x
            self._c = mqtt.Client()
        self._c.enable_logger()          # Make connection failures/reconnects visible on stderr
        self._c.connect(host, port)      # Exception on failure -- no file created (same as recorder.py)
        self._c.loop_start()

    def publish(self, topic, payload):
        self._c.publish(topic, payload, qos=0)

    def close(self):
        self._c.loop_stop()


# ---------------------------------------------------------------------------
# Camera prop setting helper
# ---------------------------------------------------------------------------

def _set_and_log(cap, prop_id, prop_name, req_val):
    """set -> get read-back result output. Continues even on failure (some webcams ignore set)."""
    import cv2
    ok = cap.set(prop_id, req_val)
    got = cap.get(prop_id)
    status = "ok" if ok else "ignored"
    print(f"[cam] {prop_name}: req={req_val} got={got} ({status})", flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--camera", type=int, default=0, help="Camera index (default 0)")
    ap.add_argument("--backend", choices=["msmf", "dshow", "any"], default="msmf",
                    help="Capture backend (default msmf -- 720p 30fps negotiation measured)")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--out", default="sessions", help="Output directory")
    ap.add_argument("--session", required=True, help="Session label (part of filename)")
    ap.add_argument("--duration", type=float, default=None, help="Recording duration in seconds -- omit for Ctrl-C")
    ap.add_argument("--status-period", type=float, default=5.0)
    ap.add_argument("--mqtt-host", default="127.0.0.1")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--no-mqtt", action="store_true", help="Skip MQTT publish (mp4 only)")
    args = ap.parse_args()

    import cv2  # cv2 imported here only -- tests don't import this file

    # ① sink setup -- NullSink if no-mqtt, otherwise paho connection
    #   (exception on failure -- no file, same as recorder.py)
    if args.no_mqtt:
        sink = NullSink()
    else:
        sink = MqttSink(args.mqtt_host, args.mqtt_port)

    # ② CamCore
    core = CamCore(sink)

    # ③ VideoCapture open -- default MSMF (measured 2026-06-11: DSHOW gets stuck at 720p YUY2 10fps
    #    due to MJPG rejection, MSMF negotiates 30fps). Camera differences handled by --backend.
    backends = {"msmf": "CAP_MSMF", "dshow": "CAP_DSHOW", "any": None}
    bk = backends[args.backend]
    if bk and hasattr(cv2, bk):
        cap = cv2.VideoCapture(args.camera, getattr(cv2, bk))
    else:
        cap = cv2.VideoCapture(args.camera)

    # ④ Prop settings + log -- MJPG before resolution (uncompressed YUY2 720p on USB2
    #    is bandwidth-limited to ~10fps -- harmless even if backend rejects it)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    cap.set(cv2.CAP_PROP_FPS, args.fps)
    # Minimize buffer -- newest frame first
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass

    # Actual read-back
    w_actual = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    h_actual = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    fps_actual = cap.get(cv2.CAP_PROP_FPS)
    fourcc_got = int(cap.get(cv2.CAP_PROP_FOURCC)) & 0xFFFFFFFF
    fourcc_str = "".join(chr((fourcc_got >> 8 * i) & 0xFF) for i in range(4))
    print(f"[cam] Resolution: req={args.width}x{args.height} got={int(w_actual)}x{int(h_actual)}", flush=True)
    print(f"[cam] fps: req={args.fps} got={fps_actual} fourcc: req=MJPG got={fourcc_str}", flush=True)

    # Attempt to disable auto exposure/focus/WB (continues even on failure)
    # 0.25=manual (DSHOW convention; 0.75=auto) -- differs by backend
    _set_and_log(cap, cv2.CAP_PROP_AUTO_EXPOSURE, "auto_exposure", 0.25)
    _set_and_log(cap, cv2.CAP_PROP_AUTOFOCUS, "autofocus", 0)
    _set_and_log(cap, cv2.CAP_PROP_AUTO_WB, "auto_wb", 0)

    # ⑤ Open mp4 after first frame read success
    #   Exit with error after 30 consecutive read failures
    first_frame = None
    t_first = None
    writer = None
    out_path = None
    exit_code = 0

    try:
        for attempt in range(30):
            ret, frame = cap.read()
            if ret:
                t_first = time.time_ns()  # Immediately after grab -- eliminates VideoWriter init delay
                first_frame = frame
                break
            # Threshold = 30 consecutive x ~frame period -- guarantees ~1s even if read returns immediately (spin prevention)
            time.sleep(1.0 / max(args.fps, 1.0))
        else:
            print("[cam] Error: could not read first frame from camera (30 attempts failed)", file=sys.stderr, flush=True)
            exit_code = 1
            return

        # Create VideoWriter with actual shape
        h_frame, w_frame = first_frame.shape[:2]
        fps_write = fps_actual if fps_actual > 0 else args.fps  # mp4 fps is playback reference -- cam/meta t_ns is the real timing source
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{args.session}-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(out_path), fourcc, fps_write, (w_frame, h_frame))
        if not writer.isOpened():
            # Prevent silent failure where write() drops frames and only cam/meta is published (asymmetric failure)
            print(f"[cam] Error: VideoWriter open failed -- check codec(mp4v)/path: {out_path}",
                  file=sys.stderr, flush=True)
            out_path = None              # File not created -- don't point summary at nothing
            exit_code = 1
            return
        print(f"[cam] Recording: {out_path}", flush=True)

        # First frame processed normally (t_first already captured right after grab)
        core.handle_frame(t_first)
        writer.write(first_frame)

        # Main loop
        t0 = time.monotonic()
        last_status = t0
        frames_at_last = core.status()["frames"]

        while True:
            ret, frame = cap.read()
            t = time.time_ns()  # Capture immediately after grab

            if ret:
                core.handle_frame(t)
                writer.write(frame)
            else:
                consec = core.note_drop()
                if consec >= 30:
                    print(f"[cam] Error: {consec} consecutive drops -- camera disconnected", file=sys.stderr, flush=True)
                    exit_code = 1
                    break
                # Threshold = 30 consecutive x ~frame period -- guarantees ~1s even if read returns immediately (spin/glitch protection)
                time.sleep(1.0 / max(args.fps, 1.0))

            now = time.monotonic()

            # Periodic status output
            if now - last_status >= args.status_period:
                st = core.status()
                elapsed = now - last_status
                frames_delta = st["frames"] - frames_at_last
                live_fps = frames_delta / elapsed if elapsed > 0 else 0.0
                print(f"[cam] {st} fps_live={live_fps:.1f}", flush=True)
                last_status = now
                frames_at_last = st["frames"]

            # Duration expiry
            if args.duration is not None and now - t0 >= args.duration:
                break

    except KeyboardInterrupt:
        print("\n[cam] stopping", flush=True)
    finally:
        cap.release()
        if writer is not None:
            writer.release()
        sink.close()
        st = core.status()
        print(f"[cam] Ended: frames={st['frames']} drops={st['drops']} -> {out_path}", flush=True)
        if exit_code != 0:
            sys.exit(exit_code)


if __name__ == "__main__":
    main()
