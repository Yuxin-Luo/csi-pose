#!/usr/bin/env python3
"""Cam fps probe — single-file FOURCC x resolution matrix test.

Examples:
  python host/tools/probe_fps.py --fourcc MJPG --width 1280 --height 720
  python host/tools/probe_fps.py --fourcc YUYV --width 640  --height 360
  for w in 1920 1280 640; do for fcc in MJPG YUYV; do
    python host/tools/probe_fps.py --fourcc $fcc --width $w --height 360
  done; done

Outputs:
  req:    what we asked cam for
  got:    what cam negotiated back
  real:   how fast frames actually arrived (timing-based, source-of-truth)

Real fps is the tiebreaker if got says 30 but real says 15 (MSMF / USB2
over-reporting).
"""
import argparse
import sys
import time
import cv2


BACKENDS = {"msmf": "CAP_MSMF", "dshow": "CAP_DSHOW", "any": None}
FOURCC = {"MJPG": "MJPG", "YUYV": "YUYV", "YUY2": "YUY2"}


def open_cam(camera, backend):
    bk = BACKENDS[backend]
    if bk and hasattr(cv2, bk):
        return cv2.VideoCapture(camera, getattr(cv2, bk))
    return cv2.VideoCapture(camera)


def fcc_decode(int_val: int) -> str:
    return "".join(chr((int_val >> 8 * i) & 0xFF) for i in range(4))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--backend", choices=list(BACKENDS), default="any")
    ap.add_argument("--fourcc", choices=list(FOURCC), required=True)
    ap.add_argument("--width", type=int, required=True)
    ap.add_argument("--height", type=int, required=True)
    ap.add_argument("--fps-req", type=float, default=30.0)
    ap.add_argument("--frames", type=int, default=200,
                    help="frames to capture (excluding warmup)")
    ap.add_argument("--timeout-s", type=float, default=15.0)
    args = ap.parse_args()

    cap = open_cam(args.camera, args.backend)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*args.fourcc))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    cap.set(cv2.CAP_PROP_FPS, args.fps_req)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass

    g_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    g_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    g_fps = cap.get(cv2.CAP_PROP_FPS)
    g_fcc = fcc_decode(int(cap.get(cv2.CAP_PROP_FOURCC)))

    print(f"[probe] req: {args.fourcc} {args.width}x{args.height} @ {args.fps_req:.0f}fps",
          flush=True)
    print(f"[probe] got: {g_fcc} {g_w}x{g_h} CAP_PROP_FPS={g_fps:.2f}", flush=True)

    # Warmup first frame (some webcams need to negotiate format)
    ok, _ = cap.read()
    if not ok:
        print("[probe] ERROR: warmup read failed", file=sys.stderr, flush=True)
        cap.release()
        return 1

    # Measure N real reads
    t0 = time.monotonic()
    n_ok = 0
    while n_ok < args.frames:
        ok, frame = cap.read()
        if not ok:
            break
        n_ok += 1
        if time.monotonic() - t0 > args.timeout_s:
            break
    elapsed = time.monotonic() - t0
    fps_real = n_ok / elapsed if elapsed > 0 else 0.0

    print(f"[probe] measured: {n_ok} frames in {elapsed:.2f}s  fps_real={fps_real:.2f}",
          flush=True)

    # Verdict
    if 28 <= fps_real <= 32:
        verdict = "OK 30fps"
    elif 14 <= fps_real <= 16:
        verdict = "OK 15fps"
    elif 28 <= g_fps <= 32 and fps_real < 20:
        verdict = f"WARN: cam reports {g_fps:.0f}fps but real {fps_real:.1f}"
    elif fps_real < 5:
        verdict = f"WARN: real fps very low"
    elif fps_real < 14:
        verdict = f"WARN: real fps < 15"
    else:
        verdict = f"INFO: real fps {fps_real:.1f}"
    print(f"[probe] verdict: {verdict}", flush=True)

    cap.release()
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
