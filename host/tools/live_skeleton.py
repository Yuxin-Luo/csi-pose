#!/usr/bin/env python3
"""Webcam live skeleton viewer — for field-of-view and tracking verification (run on Windows).

Reuses RTMDet->RTMPose runner from teacher for real-time COCO-17 skeleton overlay.
Independent of collection pipeline (no MQTT or storage).

    python host\\tools\\live_skeleton.py                # Default: cam0, MSMF, 720p, CPU
    python host\\tools\\live_skeleton.py --device cuda  # With GPU EP installed

Keys: ESC/q to quit, m to toggle mirror. First run auto-downloads models (~120MB).
NOTE: MSMF camera is single-occupancy — must quit before running cam_capture.py.
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np

# Tracking parameters (spec Section requirements — CPU real-time 2-mode loop)
SEARCH_DET_EVERY = 10   # Search mode: detection period (frames)
TRACK_DET_PERIOD = 1.0  # Track mode: detection re-check period (seconds)
KPT_THR = 0.3           # Valid keypoint/person drop threshold score
MIN_VALID_KPTS = 4      # Minimum valid keypoints for bounding box
BBOX_MARGIN = 0.2       # Bbox inheritance margin ratio
MAX_PERSONS = 3
READ_FAIL_MAX = 30      # Continuous read failure limit


def kpts_to_bbox(kpts, score_thr, margin, frame_wh):
    """(17,3)[x,y,score] -> valid keypoint bounding box with margin (frame clipped). frame_wh=(width,height). Valid <4 -> None."""
    k = np.asarray(kpts, np.float32)
    pts = k[k[:, 2] >= score_thr, :2]
    if len(pts) < MIN_VALID_KPTS:
        return None
    x1, y1 = pts.min(axis=0)
    x2, y2 = pts.max(axis=0)
    mx, my = (x2 - x1) * margin, (y2 - y1) * margin
    w, h = frame_wh
    return np.array([max(0.0, x1 - mx), max(0.0, y1 - my),
                     min(w - 1.0, x2 + mx), min(h - 1.0, y2 + my)], np.float32)


class ViewerCore:
    """Search/track 2-mode scheduler — runner is injected (cv2 GUI, camera independent).

    Search (0 tracked): detect every SEARCH_DET_EVERY frames.
    Track: pose-only each frame (bbox inherited from previous keypoint bounding box),
           detect re-check every TRACK_DET_PERIOD — handles new people and tracks end.
    """

    def __init__(self, runner, det_thr=0.5):
        self.runner = runner
        self.det_thr = det_thr
        self.bboxes = []          # Per tracked person (4,) bbox — empty means search mode
        self.det_ms = 0.0
        self.pose_ms = 0.0
        self._frame_idx = 0
        self._last_det_t = None

    @property
    def tracking(self):
        return bool(self.bboxes)

    def _det_due(self, now):
        if not self.tracking:
            return self._frame_idx % SEARCH_DET_EVERY == 0
        return now - self._last_det_t >= TRACK_DET_PERIOD

    def step(self, frame, now):
        """Process 1 frame -> (list of per-person (17,3) kpts, hud dict)."""
        if self._det_due(now):
            t0 = time.perf_counter()
            dets = self.runner.detect(frame)
            self.det_ms = (time.perf_counter() - t0) * 1e3
            self._last_det_t = now
            # runner guarantees dets sorted by score descending (runner.py detect) — [:MAX_PERSONS] is top N
            self.bboxes = [d[:4] for d in dets if d[4] >= self.det_thr][:MAX_PERSONS]

        h, w = frame.shape[:2]
        persons, next_bboxes = [], []
        t0 = time.perf_counter()
        for bbox in self.bboxes:
            kpts = self.runner.pose(frame, bbox)
            if kpts[:, 2].mean() < KPT_THR:
                continue                      # Drop low-confidence person — if all lost, return to search
            nb = kpts_to_bbox(kpts, KPT_THR, BBOX_MARGIN, (w, h))
            if nb is None:
                continue
            persons.append(kpts)
            next_bboxes.append(nb)
        self.pose_ms = (time.perf_counter() - t0) * 1e3
        self.bboxes = next_bboxes
        self._frame_idx += 1
        return persons, {"det_ms": self.det_ms, "pose_ms": self.pose_ms,
                         "n": len(persons), "tracking": self.tracking}


def build_parser():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", type=int, default=0, help="Camera index (default 0)")
    ap.add_argument("--backend", choices=["msmf", "dshow", "any"], default="msmf",
                    help="cv2 capture backend (same as cam_capture — MSMF negotiates 30fps)")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--device", default="cpu", choices=["cpu", "cuda", "auto"],
                    help="Inference device — Windows default cpu (auto tries CUDA then falls back, causing double model init)")
    ap.add_argument("--det-thr", type=float, default=0.5, dest="det_thr",
                    help="Person detection score threshold (default 0.5)")
    ap.add_argument("--mirror", action="store_true", help="Start with mirror display (toggle with m)")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)

    try:
        import cv2
        from rtmlib import draw_skeleton
    except ImportError as e:                  # Not installed guide (spec Section prerequisites)
        print(f"Error: dependency not installed ({e.name}) — in Windows PowerShell run:\n"
              "  pip install --no-deps rtmlib==0.0.15\n"
              "  pip install onnxruntime tqdm", file=sys.stderr)
        return 1

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "teacher"))
    from csi_teacher.runner import make_runner

    print("[live] Initializing runner — first run auto-downloads models (~120MB)", flush=True)
    core = ViewerCore(make_runner(device=args.device), det_thr=args.det_thr)

    # Camera open — same as cam_capture.py Section 3,4 (MSMF default, MJPG first, buffer 1)
    backends = {"msmf": "CAP_MSMF", "dshow": "CAP_DSHOW", "any": None}
    bk = backends[args.backend]
    cap = (cv2.VideoCapture(args.camera, getattr(cv2, bk))
           if bk and hasattr(cv2, bk) else cv2.VideoCapture(args.camera))
    if not cap.isOpened():
        print(f"Error: failed to open camera {args.camera} — check --camera/--backend, "
              "close occupying processes (cam_capture etc.)", file=sys.stderr)
        return 1
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # Newest frame first
    except Exception:
        pass

    mirror = args.mirror
    fps = 0.0
    t_prev = time.monotonic()
    fails = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                fails += 1
                if fails >= READ_FAIL_MAX:
                    print(f"Error: frame read failed {READ_FAIL_MAX} consecutive times",
                          file=sys.stderr)
                    return 1
                continue
            fails = 0
            if mirror:
                frame = cv2.flip(frame, 1)    # Flip before inference to match coordinate system

            now = time.monotonic()
            persons, hud = core.step(frame, now)
            dt = max(now - t_prev, 1e-6)
            t_prev = now
            fps = (1.0 / dt) if fps == 0.0 else fps * 0.9 + (1.0 / dt) * 0.1

            if persons:
                k = np.stack(persons)         # draw_skeleton only accepts batched (N,17,...) input
                frame = draw_skeleton(frame, k[:, :, :2], k[:, :, 2],
                                      openpose_skeleton=False, kpt_thr=KPT_THR)
            txt = (f"fps {fps:4.1f}  det {hud['det_ms']:5.1f}ms  "
                   f"pose {hud['pose_ms']:5.1f}ms  N={hud['n']}  {args.device}"
                   f"{'  MIRROR' if mirror else ''}")
            cv2.putText(frame, txt, (8, 24), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (0, 255, 0), 1, cv2.LINE_AA)
            cv2.putText(frame, "[ESC/q] quit  [m] mirror",
                        (8, frame.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (200, 200, 200), 1, cv2.LINE_AA)
            cv2.imshow("live_skeleton", frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                break
            if key == ord("m"):
                mirror = not mirror
                core.bboxes = []              # Coordinate system flipped — invalidate inherited bboxes
            if cv2.getWindowProperty("live_skeleton", cv2.WND_PROP_VISIBLE) < 1:
                break                         # X button closed — exit before next imshow recreates window
    finally:
        cap.release()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
