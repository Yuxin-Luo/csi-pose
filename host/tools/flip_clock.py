#!/usr/bin/env python3
"""Monitor black/white flip indicator -- for camera system offset measurement.

Full-screen cv2 window shows black(0) <-> white(255) transitions, records time.time_ns() right before each flip.
Interval 0.7s +- 30% jitter, N=40 flips.

  python3 flip_clock.py --out /tmp/flips.json
  python3 flip_clock.py --n 20 --interval 0.7 --jitter 0.3 --out flips.json
"""  # noqa: E501
import argparse
import json
import sys
import time


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="flip_times.json", help="Flip times JSON save path")
    ap.add_argument("--n", type=int, default=40, help="Number of flips (default 40)")
    ap.add_argument("--interval", type=float, default=0.7, help="Average interval in seconds (default 0.7)")
    ap.add_argument("--jitter", type=float, default=0.3, help="Interval jitter ratio (default 0.3 = +-30%%)")
    ap.add_argument("--seed", type=int, default=None, help="Random seed (for reproducibility)")
    args = ap.parse_args()

    # cv2 imported on demand (--help works without cv2)
    import random
    import numpy as np

    if args.seed is not None:
        random.seed(args.seed)
        np.random.seed(args.seed)

    try:
        import cv2
    except ImportError:
        print("Error: cv2 not installed. pip install opencv-python", file=sys.stderr)
        sys.exit(1)

    # Full-screen black window init
    WIN = "flip_clock"
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    state = 0  # 0=black, 1=white
    flip_times_ns = []

    # Pre-generate jitter intervals
    rng = np.random.default_rng(args.seed)
    half = args.interval * args.jitter
    waits = rng.uniform(args.interval - half, args.interval + half, args.n)

    try:
        for i, wait_s in enumerate(waits):
            # Wait
            deadline = time.monotonic() + wait_s
            while time.monotonic() < deadline:
                img = np.zeros((100, 100, 3), np.uint8) if state == 0 else np.full((100, 100, 3), 255, np.uint8)
                cv2.imshow(WIN, img)
                if cv2.waitKey(1) == 27:  # ESC -> abort (finally saves and cleans up)
                    print(f"ESC abort ({i}/{args.n} done)", file=sys.stderr)
                    return

            # Record time right before flip -> switch
            t_ns = time.time_ns()
            flip_times_ns.append(t_ns)
            state = 1 - state
            img = np.zeros((100, 100, 3), np.uint8) if state == 0 else np.full((100, 100, 3), 255, np.uint8)
            cv2.imshow(WIN, img)
            cv2.waitKey(1)
            print(f"Flip {i+1:02d}/{args.n}", end="\r", flush=True)

        print(f"\n{args.n} flips done")
    finally:
        # All exit paths (exception, ESC, normal) guarantee fullscreen window cleanup + (partial) data save
        # -- whether N is incomplete is determined by analysis side via JSON n
        cv2.destroyAllWindows()
        _save(args.out, flip_times_ns)


def _save(path, flip_times_ns):
    data = {"flip_times_ns": flip_times_ns, "n": len(flip_times_ns)}
    import pathlib
    pathlib.Path(path).write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"Saved: {path}  ({len(flip_times_ns)} flips)")


if __name__ == "__main__":
    main()
