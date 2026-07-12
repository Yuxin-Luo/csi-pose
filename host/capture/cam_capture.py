#!/usr/bin/env python3
"""Webcam capture -- mp4 local recording + MQTT cam/meta {frame_idx, t_ns} publication.

  python cam_capture.py --out ..\\sessions --session s01-r1 [--duration 600] [--no-mqtt]

t_ns is host clock right after grab (same principle as bridge) -- offset from exposure time
is measured by LED alignment verification.
Auto exposure/focus/WB attempts to disable and logs request vs actual (Section 3.3 -- some webcams ignore set).
Required packages: opencv-python, msgpack, paho-mqtt.

--skeleton (default ON): draws COCO-17 skeleton + bbox on the LIVE PREVIEW window
only. Recorded mp4 keeps segment overlay but NO skeleton (teacher.py reads mp4
later -- pre-drawn skeletons would re-trigger RTMPose on already-labelled pixels).
Requires rtmlib + onnxruntime. First run downloads ~145MB to ~/.cache/rtmlib/.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # host/
from csi_host.cam_core import CamCore  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))  # host/capture/
from plan import parse_plan, PlanState, draw_overlay  # noqa: E402

# --- Skeleton preview constants (mirrors host/tools/live_skeleton.py) ----
# Search/track 2-mode simplified for cam_capture: detect every SEARCH_DET_EVERY
# frames, pose every frame on the most-recent bboxes. No track-mode bbox
# inheritance because the preview is short-lived (user watches for ~10 min).
SEARCH_DET_EVERY = 5    # Lower than live_skeleton (10) -- fresh start each run, no Track mode
SKEL_DET_THR = 0.5      # RTMDet person score threshold
KPT_THR = 0.3           # RTMPose keypoint score threshold (drop person if avg below)
MAX_PERSONS = 3         # COCO usually 1-2 people, 3 covers edge cases

# --- Preview-only HUD layout (NEVER touches mp4) ---------------------------
# Top-left: live fps (always on while recording). Bottom-left: skeleton stats
# (only when --skeleton). Segment overlay (top-right) lives on preview as well.
# All HUD drawing targets the `preview` copy; `frame` going to writer.write()
# stays 100% raw -- matches upstream cam_capture.py author intent.
FPS_HUD_POS = (8, 28)
FPS_HUD_SCALE = 0.8
FPS_HUD_COLOR = (0, 255, 255)   # BGR yellow — high visibility on top of any frame
FPS_HUD_THICK = 2
LIVE_FPS_WINDOW_S = 1.0         # rolling window for live fps


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
    ap.add_argument("--width", type=int, default=640,
                    help="Capture width (USB2 bandwidth capped — 640 tested at 30fps, 720p capped at 15)")
    ap.add_argument("--height", type=int, default=360,
                    help="Capture height (USB2 bandwidth capped — 360 tested at 30fps, 720p capped at 15)")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--out", default="sessions", help="Output directory")
    ap.add_argument("--session", required=True, help="Session label (part of filename)")
    ap.add_argument("--duration", type=float, default=None, help="Recording duration in seconds -- omit for Ctrl-C")
    ap.add_argument("--status-period", type=float, default=5.0)
    ap.add_argument("--start-on-key", action="store_true", help="Wait for Enter before recording")
    ap.add_argument("--plan", default=None, help='Plan string "1:label:60,2:label:40,..."')
    ap.add_argument("--overlay", action="store_true", default=True, help="Draw segment overlay")
    ap.add_argument("--no-overlay", dest="overlay", action="store_false")
    ap.add_argument("--skeleton", action="store_true", default=True,
                    help="Draw COCO-17 skeleton + bbox on the LIVE PREVIEW (default ON; mp4 stays raw)")
    ap.add_argument("--no-skeleton", dest="skeleton", action="store_false",
                    help="Disable live skeleton preview (mp4 is unaffected either way)")
    ap.add_argument("--skeleton-device", default="cpu", choices=["cpu", "cuda", "auto"],
                    help="RTMDet+RTMPose inference device (default cpu)")
    ap.add_argument("--mqtt-host", default="127.0.0.1")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--no-mqtt", action="store_true", help="Skip MQTT publish (mp4 only)")
    args = ap.parse_args()
    plan_list = parse_plan(args.plan) if args.plan else []
    plan_state = PlanState(plan_list) if plan_list else None

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

        # ②.5 Preview window + Gate
        # Use cv2.waitKey (GUI key) instead of input() — &-launched subprocess stdin
        # can be closed/EOF on this Linux setup (Qt event loop grabs stdin in cv2.waitKey),
        # so input() raises EOFError immediately. cv2.waitKey is independent of stdin.
        cv2.namedWindow("cam", cv2.WINDOW_NORMAL)
        preview = first_frame.copy()
        cv2.putText(preview, "PRESS ANY KEY TO START", (50, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 255), 3)
        cv2.putText(preview, "(make sure you're in frame!)", (50, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.putText(preview, "(or Ctrl-C to abort)", (50, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 200), 2)
        cv2.imshow("cam", preview)
        cv2.waitKey(100)
        if args.start_on_key:
            print("[cam] Press ANY KEY in the cam preview window to start recording...",
                  flush=True)
            while True:
                k = cv2.waitKey(0) & 0xFF
                if k != 0xFF:                        # 0xFF = no key pressed (window not focused)
                    print(f"[cam] Got key 0x{k:02x}, starting recording", flush=True)
                    # Touch sentinel file so recorder (waiting on it) unblocks in sync
                    # (path is fixed: same --out + sentinel name as recorder.py expects)
                    gate_flag = Path(args.out) / f".{args.session}.gate"
                    gate_flag.parent.mkdir(parents=True, exist_ok=True)
                    gate_flag.touch()
                    break

        # ②.65 Lazy-init skeleton runner (post-gate so user doesn't wait for
        # ~145MB model download BEFORE they even know they're ready to record).
        # draw_skeleton from rtmlib; runner from teacher/csi_teacher/runner.py
        # (same RTMDet+RTMPose pair used by live_skeleton.py and teacher.py).
        skel_runner = None
        skel_draw = None
        if args.skeleton:
            try:
                from rtmlib import draw_skeleton as _draw_skeleton
                skel_draw = _draw_skeleton
            except ImportError as e:
                print(f"[cam] ERROR: --skeleton requires rtmlib ({e.name}). "
                      f"Install: pip install --no-deps rtmlib==0.0.15 onnxruntime tqdm",
                      file=sys.stderr, flush=True)
                sys.exit(1)
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "teacher"))
                from csi_teacher.runner import make_runner
                print(f"[cam] skeleton: loading RTMDet+RTMPose on {args.skeleton_device} "
                      f"(first run downloads ~145MB to ~/.cache/rtmlib/)", flush=True)
                skel_runner = make_runner(device=args.skeleton_device)
                print("[cam] skeleton: runner ready", flush=True)
            except Exception as e:
                print(f"[cam] ERROR: skeleton runner init failed: {type(e).__name__}: {e}",
                      file=sys.stderr, flush=True)
                sys.exit(1)
        skel_bboxes = []           # Last RTMDet boxes (inherited across frames, refreshed every SEARCH_DET_EVERY)
        skel_frame_idx = 0         # Frame counter (mod SEARCH_DET_EVERY triggers re-detect)
        skel_det_ms = 0.0          # Last detect latency (HUD)
        skel_pose_ms = 0.0         # Last pose batch latency (HUD)

        # Live fps HUD state (always-on top-left of preview)
        live_fps = 0.0
        _fps_count = 0
        _fps_t0 = time.monotonic()

        # ②.7 Calibrate real fps from a brief capture burst (~1s on a 30fps cam).
        # cap.grab() skips decode so measurement reflects pure bus throughput.
        # 1s boot-cost is acceptable: user just pressed a key.
        # Clamp to [10, 60] to avoid pathological values (one-frame returns
        # would otherwise yield ridiculous "1000+ fps").
        calib_n = 30
        t_calib_t0 = time.monotonic()
        for _ in range(calib_n):
            cap.grab()
        calib_elapsed = time.monotonic() - t_calib_t0
        fps_calib = calib_n / calib_elapsed if calib_elapsed > 0 else 0.0
        fps_calib = max(10.0, min(60.0, fps_calib))
        print(f"[cam] Calibrated fps: real={fps_calib:.2f} (CAP_PROP_FPS reported={fps_actual:.2f})",
              flush=True)

        # Create VideoWriter with actual shape
        h_frame, w_frame = first_frame.shape[:2]
        fps_write = fps_calib if fps_calib > 0 else args.fps  # Measured real fps, not negotiated
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
                # -- Plan state machine ---------------------------------------------
                # Tick regardless of args.overlay so segment transitions stay
                # reproducible from `[cam] segment N/M -> label` log lines; only
                # the visual overlay on the preview is gated by args.overlay.
                elapsed = 0.0
                if plan_state is not None:
                    if plan_state.seg_start is None:
                        plan_state.seg_start = time.monotonic()
                    elapsed = time.monotonic() - plan_state.seg_start
                    if plan_state.tick(time.monotonic()):
                        print(f"[cam] segment {plan_state.cur_seg + 1}/{plan_state.total_segments} -> {plan_state.cur_label}", flush=True)
                        plan_state.seg_start = time.monotonic()
                        elapsed = 0.0

                core.handle_frame(t)
                writer.write(frame)        # mp4 = 100% raw (no cv2 ever touches frame)

                # --- Live preview: ALWAYS on a separate `preview` copy -------------
                # All cv2.putText / draw_skeleton / draw_overlay below operate
                # ONLY on `preview`. frame was already serialized to mp4 above,
                # so no further cv2 call can leak into the mp4 stream. This
                # satisfies the upstream author intent of mp4 = raw frames.
                preview = frame.copy()

                # Roll live fps (window = LIVE_FPS_WINDOW_S seconds, recomputed each flush)
                _fps_count += 1
                _now = time.monotonic()
                if _now - _fps_t0 >= LIVE_FPS_WINDOW_S:
                    live_fps = _fps_count / (_now - _fps_t0)
                    _fps_count = 0
                    _fps_t0 = _now

                # Top-left: live fps — always shown on preview
                cv2.putText(preview, f"FPS {live_fps:4.1f}", FPS_HUD_POS,
                            cv2.FONT_HERSHEY_SIMPLEX, FPS_HUD_SCALE,
                            FPS_HUD_COLOR, FPS_HUD_THICK, cv2.LINE_AA)

                # Segment overlay (top-right) on PREVIEW only
                if plan_state is not None and args.overlay:
                    draw_overlay(preview, plan_state, elapsed)

                # Skeleton + bottom-left stats on PREVIEW only
                if args.skeleton and skel_runner is not None:
                    import numpy as np   # local: cam_capture used standalone too
                    # Search mode: detect every SEARCH_DET_EVERY frames, PERIODIC.
                    # No "if not skel_bboxes" fallback — that would detect every
                    # frame when no one is in shot and pin fps at ~10 (RTMDet
                    # ~100ms/frame). Upstream live_skeleton.py uses pure
                    # time-based _det_due() for the same reason. Stale bboxes
                    # for ≤(SEARCH_DET_EVERY-1) frames are filtered out by the
                    # KPT_THR=0.3 mean-score gate in the pose loop below.
                    if skel_frame_idx % SEARCH_DET_EVERY == 0:
                        t0 = time.perf_counter()
                        dets = skel_runner.detect(frame)
                        skel_det_ms = (time.perf_counter() - t0) * 1e3
                        skel_bboxes = [d[:4] for d in dets if d[4] >= SKEL_DET_THR][:MAX_PERSONS]
                    # Pose each tracked bbox (search-mode bboxes also work; pose is cheap)
                    persons = []
                    t0 = time.perf_counter()
                    for bbox in skel_bboxes:
                        kpts = skel_runner.pose(frame, bbox)
                        if kpts[:, 2].mean() >= KPT_THR:
                            persons.append(kpts)
                    skel_pose_ms = (time.perf_counter() - t0) * 1e3
                    skel_frame_idx += 1

                    if persons:
                        k = np.stack(persons)
                        # draw_skeleton returns a NEW ndarray — assign to `preview`
                        preview = skel_draw(preview, k[:, :, :2], k[:, :, 2],
                                            openpose_skeleton=False, kpt_thr=KPT_THR)
                    hud = f"skel N={len(persons)}  det={skel_det_ms:.0f}ms  pose={skel_pose_ms:.0f}ms"
                    color = (0, 255, 0) if persons else (0, 100, 255)
                    cv2.putText(preview, hud, (8, preview.shape[0] - 8),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)

                cv2.imshow("cam", preview)
                cv2.waitKey(1)
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
