#!/usr/bin/env python3
"""Alignment verification CLI -- (STOPx20, monitor flip, jitter 3 components).

Modes:
  --mode csi     Send STOP/START to tx0 port N times -> clock-fit gap vs send time statistics
  --mode cam     flip JSON + mp4 + session HDF5 -> camera system offset calculation
                 (verify cam_capture MQTT publish + recorder session with flip_clock running --
                  the cam/meta t_ns stamp used for training pairing is being verified)
  --mode jitter  Existing session HDF5 + clock-fit residuals -> jitter statistics
  --mode report  Merge 3 result JSONs -> Section 13 verdict output

Caution: rawlog from --mode csi verification run should be stored separately
(verification gaps are intentional -- do not mix with soak scoring files).

Example:
  python3 align_verify.py --mode csi --port COM34 \
      --rawlog /tmp/v_rx0.rawlog /tmp/v_rx1.rawlog /tmp/v_rx2.rawlog
  python3 align_verify.py --mode cam --video cam.mp4 --session session.h5 \
      --flips flip_times.json
  python3 align_verify.py --mode jitter --hdf5 session.h5
  python3 align_verify.py --mode report --csi-json csi_result.json \
      --cam-json cam_result.json --jitter-json jitter_result.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from csi_pipe.align_verify import (  # noqa: E402
    camera_correction_ms,
    csi_absolute_offsets,
    detect_gaps,
    flip_offsets,
    jitter_stats,
    match_frames_by_idx,
    verdict,
)


def _out_path(args, suffix):
    """Default prefix + suffix for --out -> save path."""
    base = getattr(args, "out", None) or "align"
    return Path(str(base) + suffix)


# -- mode csi --

def mode_csi(args):
    """Send STOP/START N times to tx0 serial port, extract clock-fit gaps from 3 rawlogs, statistics."""
    # pyserial only needed for actual connection -- delayed import
    import serial
    import random
    import numpy as np
    from csi_pipe.soak import collect_rawlog
    from csi_pipe.clockfit import fit_board

    port = args.port
    baud = args.baud
    n_shots = args.n
    rawlog_paths = [Path(p) for p in args.rawlog]
    rate = args.rate
    interval = args.interval
    jitter_ratio = args.shot_jitter

    print(f"Port: {port}  rawlog: {[str(p) for p in rawlog_paths]}  N={n_shots}", flush=True)

    cmd_times_ns = []

    with serial.Serial(port, baud, timeout=0.2) as ser:
        try:
            for i in range(n_shots):
                # Record time right before sending
                t_ns = time.time_ns()
                cmd_times_ns.append(t_ns)
                ser.write(b"STOP\n")
                time.sleep(0.1)
                ser.write(f"START rate={rate}\n".encode())
                print(f"  Shot {i+1:02d}/{n_shots}", end="\r", flush=True)

                # Jitter wait until next shot
                half = interval * jitter_ratio
                wait = random.uniform(interval - half, interval + half)
                time.sleep(wait)
        finally:
            # Guarantee tx0 transmission resumes on any exit path (Ctrl-C, SerialException included) --
            # prevent tx0 silence if stopped between STOP and START
            try:
                time.sleep(0.1)
                ser.write(f"START rate={rate}\n".encode())
            except Exception as e:
                print(f"Warning: final START reinjection failed -- may need manual 'START rate={rate}' injection: {e}", file=sys.stderr)

    print(f"\nShot complete -- analyzing {len(rawlog_paths)} rawlogs", flush=True)

    # Per rawlog (=RX board) -> full frame clock-fit -> tx0 link corrected time -> 3 RX median clustering
    t_fit_by_rx = {}
    for p in rawlog_paths:
        board = collect_rawlog(p)
        if board.frames < 200:
            print(f"Warning: insufficient samples (<200 frames) -- {p}", file=sys.stderr)
            continue
        # Fit uses all frames (tx1/2 keep flowing during STOP so fit domain isn't interrupted),
        # gap detection only on tx0 link corrected time
        _, rep = fit_board(
            board.esp_us.astype(np.float64),
            board.t_ns,
            board.boot,
        )
        rx_id = int(np.bincount(board.rx_ids).argmax())  # Dominant rx_id (soak pattern)
        m = rep.valid & (board.tx == 0)
        if rx_id in t_fit_by_rx:
            print(f"Warning: duplicate rawlog for rx{rx_id} -- replacing with later file: {p}", file=sys.stderr)
        t_fit_by_rx[rx_id] = rep.t_fit[m]

    cmd_arr = np.asarray(cmd_times_ns, dtype=np.float64)
    gaps = detect_gaps(t_fit_by_rx)
    result = csi_absolute_offsets(gaps, cmd_arr)
    # Beacon period -- needed for csi_jitter (sqrt(n*se^2 - T^2/12)) calculation in verdict
    result["rate"] = rate
    result["period_ms"] = 1000.0 / rate

    # Results output
    print(f"\n[CSI absolute offset]  n={result['n']}  mean={result['mean_ms']:.2f}ms"
          f"  se={result['se_ms']:.2f}ms  p5/p95={result['p5']:.1f}/{result['p95']:.1f}ms"
          f"  matched={result['matched']}  unmatched={result['unmatched']}")
    gate = result["se_ms"] < 2.0 and abs(result["mean_ms"]) < 10.0
    print(f"  Gate SE<2ms AND |mean|<10ms (v1.5.1): {'PASS' if gate else 'FAIL'}"
          f"  -- mean is recorded as CSI correction value")

    # cmd_times JSON saved (reused by later report mode)
    out_cmd = _out_path(args, "_cmd_times.json")
    out_csi = _out_path(args, "_csi_result.json")
    out_cmd.write_text(json.dumps({"cmd_times_ns": cmd_times_ns}, indent=1), encoding="utf-8")
    out_csi.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"Saved: {out_csi}")
    return result


# -- mode cam --

def mode_cam(args):
    """flip JSON + mp4 + session HDF5 -> absolute t_ns alignment -> flip_offsets statistics.

    Frame times use session cam/meta stamp (video_t_ns) as-is -- mp4 sequence k =
    frame_idx k (cam_capture publishes and records as a pair) for ordering.
    This validates the exact stamp used for training pairing, giving accurate measurement validity.
    """
    import cv2  # Delayed import -- only needed in this mode
    import numpy as np
    from csi_pipe.store import SessionReader

    flip_data = json.loads(Path(args.flips).read_text(encoding="utf-8"))
    flip_times = np.asarray(flip_data["flip_times_ns"], dtype=np.int64)

    # Extract per-frame average brightness from mp4 (sequence = frame_idx)
    cap = cv2.VideoCapture(str(args.video))
    brightness_list = []
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        brightness_list.append(float(gray.mean()))
    cap.release()
    brightness = np.asarray(brightness_list, dtype=np.float64)

    # Absolute stamps from session HDF5 (recorder loss = possible subset)
    with SessionReader(args.session) as sr:
        video_t_ns = sr.video_t_ns
        video_idx = sr.video_frame_idx
    if video_idx is None:  # Legacy session fallback -- identity (store.py contract)
        video_idx = np.arange(len(video_t_ns))

    frame_t, fb = match_frames_by_idx(brightness, video_idx, video_t_ns)
    print(f"mp4 {len(brightness)} frames / session {len(video_t_ns)} stamps "
          f"-> aligned {len(frame_t)} pairs", flush=True)

    result = flip_offsets(flip_times, frame_t, fb)

    # Correction formula: mean - display latency - T_frame/2 (T_frame = measured interval median)
    if result["n"] > 0:
        result["correction_ms"] = camera_correction_ms(
            result["mean_ms"], frame_t, display_latency_ms=args.display_latency)

    print(f"[Camera offset]  n={result['n']}  raw_mean={result.get('mean_ms', float('nan')):.1f}ms"
          f"  correction={result.get('correction_ms', float('nan')):.1f}ms"
          f"  residual uncertainty +-15ms (display latency uncertainty dominant)")

    out = _out_path(args, "_cam_result.json")
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"Saved: {out}")
    return result


# -- mode jitter --

def mode_jitter(args):
    """HDF5 cam/meta t_ns + clock-fit residuals -> jitter_stats."""
    import numpy as np

    try:
        from csi_pipe.store import SessionReader
    except ImportError:
        print("Error: h5py not installed. pip install h5py", file=sys.stderr)
        sys.exit(1)

    with SessionReader(args.hdf5) as sr:
        cam_t = sr.video_t_ns

    # Clock-fit residuals: recalculate per board from rawlogs if available, then combine; empty array otherwise
    from csi_pipe.soak import collect_rawlog
    from csi_pipe.clockfit import fit_board

    resid_parts = []
    for p in (args.rawlog or []):
        board = collect_rawlog(Path(p))
        if board.frames < 200:
            continue
        _, rep = fit_board(
            board.esp_us.astype(np.float64),
            board.t_ns,
            board.boot,
        )
        resid_parts.append(rep.resid_ns[rep.valid] / 1_000_000)
    resid_ms = np.concatenate(resid_parts) if resid_parts else np.zeros(0)

    result = jitter_stats(cam_t, resid_ms)

    cam_ok = result["cam_sigma_ms"] < 10.0
    print(f"[Jitter]  cam_sigma={result['cam_sigma_ms']:.2f}ms (gate <10ms: "
          f"{'PASS' if cam_ok else 'FAIL'})"
          f"  cam_p95={result['cam_interval_p95_ms']:.2f}ms"
          f"  clockfit_resid_p95={result['clockfit_resid_p95_ms']:.2f}ms"
          f"  (reference only -- bridge chunk distribution, excluded from v1.5.1 gate. CSI verdict is --mode report)")

    out = _out_path(args, "_jitter_result.json")
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"Saved: {out}")
    return result


# -- mode report --

def mode_report(args):
    """Merge 3 result JSONs -> verdict."""
    csi = json.loads(Path(args.csi_json).read_text(encoding="utf-8"))
    jit = json.loads(Path(args.jitter_json).read_text(encoding="utf-8"))

    flip_res = None
    if args.cam_json and Path(args.cam_json).exists():
        flip_res = json.loads(Path(args.cam_json).read_text(encoding="utf-8"))

    v = verdict(csi, jit, flip_result=flip_res)

    print("\n=== Section 13 M1 v1.5.1 Alignment Verdict ===")
    print(f"  CSI absolute  : {'PASS' if v['csi_ok'] else 'FAIL'}"
          f"  (mean={csi.get('mean_ms', float('nan')):.2f}ms,"
          f" se={csi.get('se_ms', float('nan')):.2f}ms,"
          f" gate SE<2 AND |mean|<10 -- mean is CSI correction value)")
    print(f"  Jitter      : {'PASS' if v['jitter_ok'] else 'FAIL'}"
          f"  (cam_sigma={jit.get('cam_sigma_ms', 0):.2f}ms,"
          f" csi_jitter={v.get('csi_jitter_ms', float('nan')):.2f}ms -- both <10ms;"
          f" clockfit resid p95={jit.get('clockfit_resid_p95_ms', 0):.1f}ms is reference)")
    if flip_res:
        corr = v.get("correction_ms", flip_res.get("correction_ms", float("nan")))
        print(f"  Camera offset: correction={corr:.1f}ms  (residual uncertainty +-15ms, no gate)")
    print(f"  Overall: {'PASS' if v['pass'] else 'FAIL'}")

    out = _out_path(args, "_verdict.json")
    out.write_text(json.dumps(v, indent=1), encoding="utf-8")
    print(f"Saved: {out}")
    return v


# -- main --

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", required=True, choices=["csi", "cam", "jitter", "report"],
                    help="Operation mode")
    ap.add_argument("--out", default="align", help="Output file prefix (default: align)")

    # csi mode
    g = ap.add_argument_group("--mode csi")
    g.add_argument("--port", default=None, help="tx0 serial port (e.g.: COM34)")
    g.add_argument("--baud", type=int, default=921600)
    g.add_argument("--n", type=int, default=20, help="STOP/START shot count (default 20)")
    g.add_argument("--rate", type=int, default=103, help="Transmission rate after START (default 103)")
    g.add_argument("--interval", type=float, default=1.7, help="Shot interval in seconds (default 1.7)")
    g.add_argument("--shot-jitter", type=float, default=0.3, help="Interval jitter ratio (default 0.3)")
    g.add_argument("--rawlog", nargs="+", default=None,
                   help="CSI rawlog paths (rx0/1/2 -- need all 3 for 3 RX median clustering)")

    # cam mode
    g2 = ap.add_argument_group("--mode cam")
    g2.add_argument("--flips", default=None, help="flip_clock.py output JSON")
    g2.add_argument("--video", default=None, help="Recording mp4 path")
    g2.add_argument("--session", default=None,
                    help="Recorder session HDF5 (cam/meta absolute t_ns source)")
    g2.add_argument("--display-latency", type=float, default=13.0,
                    help="Display latency to subtract in ms (default 13)")

    # jitter mode
    g3 = ap.add_argument_group("--mode jitter")
    g3.add_argument("--hdf5", default=None, help="Session HDF5 path")
    # --rawlog shared

    # report mode
    g4 = ap.add_argument_group("--mode report")
    g4.add_argument("--csi-json", default=None, help="csi result JSON")
    g4.add_argument("--cam-json", default=None, help="cam result JSON (optional)")
    g4.add_argument("--jitter-json", default=None, help="jitter result JSON")

    args = ap.parse_args()

    if args.mode == "csi":
        if not args.port:
            ap.error("--mode csi requires --port")
        if not args.rawlog:
            ap.error("--mode csi requires --rawlog")
        mode_csi(args)
    elif args.mode == "cam":
        if not args.flips:
            ap.error("--mode cam requires --flips")
        if not args.video:
            ap.error("--mode cam requires --video")
        if not args.session:
            ap.error("--mode cam requires --session")
        mode_cam(args)
    elif args.mode == "jitter":
        if not args.hdf5:
            ap.error("--mode jitter requires --hdf5")
        mode_jitter(args)
    elif args.mode == "report":
        if not args.csi_json or not args.jitter_json:
            ap.error("--mode report requires --csi-json, --jitter-json")
        mode_report(args)


if __name__ == "__main__":
    main()
