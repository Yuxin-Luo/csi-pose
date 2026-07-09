#!/usr/bin/env python3
"""Teacher labeling CLI — label | qa | gate | pam.

From WSL2's teacher/ directory:
  python3 teacher.py label ../host/sessions/S.mp4 --h5 ../host/sessions/S.h5
  python3 teacher.py qa --h5 ../host/sessions/S.h5 --out qa_s01      # S04 uses --all
  python3 teacher.py gate qa_s01/verdicts.json qa_s04/verdicts.json
  python3 teacher.py pam --h5 ../host/sessions/S.h5 --verdicts qa_s01/verdicts.json
"""
import argparse
import sys
import time
from pathlib import Path

import cv2
import h5py

from csi_teacher import labels as L
from csi_teacher import qa as Q
from csi_teacher.pam import build_pam


def iter_frames(mp4):
    cap = cv2.VideoCapture(str(mp4))
    if not cap.isOpened():
        raise SystemExit(f"Failed to open mp4: {mp4}")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                return
            yield frame
    finally:
        cap.release()


def cmd_label(a, make_runner=None):
    if make_runner is None:
        from csi_teacher.runner import make_runner   # Delayed — rtmlib not installed should not block other subcommands
    runner = make_runner(a.device)
    t0 = time.monotonic()
    res = L.run_label(
        iter_frames(a.mp4), runner, det_thr=a.det_thr,
        progress=lambda n: print(
            f"  {n} frames {n / max(time.monotonic() - t0, 1e-9):.1f}fps", flush=True))
    res.attrs.update(L.base_attrs(mp4=a.mp4, det_thr=a.det_thr,
                                  det_model=getattr(runner, "det_model", "?"),
                                  pose_model=getattr(runner, "pose_model", "?")))
    F = len(res.status)
    if a.h5:
        L.write_h5(a.h5, res, force=a.force)
        dst = a.h5
    else:
        side = Path(a.mp4).with_suffix(".labels.npz")
        if side.exists() and not a.force:
            raise SystemExit(f"Existing {side} — use --force to re-label")
        L.save_npz(side, res)
        dst = side
    dt = max(time.monotonic() - t0, 1e-9)
    print(f"Saved: {dst} (F={F}, {F / dt:.1f}fps) — "
          f"ok={int((res.status == L.STATUS_OK).sum())} "
          f"no_person={int((res.status == L.STATUS_NO_PERSON).sum())} "
          f"multi={int((res.status == L.STATUS_MULTI).sum())}")


def cmd_qa(a):
    if a.h5:
        with h5py.File(a.h5, "r") as h:
            if "labels" not in h:
                raise SystemExit("/labels not found — run label first")
            g = h["labels"]
            pose18, status = g["pose18"][...], g["status"][...]
            det = g["det_score"][...]
            mp4 = a.mp4 or g.attrs.get("mp4")
    else:
        res = L.load_npz(a.npz)
        pose18, status, det = res.pose18, res.status, res.det_score
        mp4 = a.mp4 or res.attrs.get("mp4")
    if not mp4 or not Path(mp4).exists():
        raise SystemExit(f"mp4 not found: {mp4!r} — specify with --mp4")
    idxs = Q.pick_frames(len(status), k=a.sample, seed=a.seed, all_frames=a.all)
    page = Q.build_gallery(a.out, mp4, pose18, status, det, idxs,
                           gid=Path(a.out).name)
    print(f"Gallery {len(idxs)} frames: {page}")
    print("Open in Windows browser, judge o/x -> 'Export JSON' -> teacher.py gate")


def cmd_gate(a):
    per, judged, fails, rate = Q.aggregate(a.verdicts)
    for p, n, f in per:
        print(f"  {p}: {f}/{n} fail")
    ok = Q.gate_pass(rate)
    print(f"Fail rate {rate:.2%} ({fails}/{judged}) — Section 7 gate (<{Q.GATE_MAX:.0%}): "
          f"{'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def cmd_pam(a):
    r = build_pam(a.h5, verdicts=a.verdicts, force=a.force)
    print(f"Done: N={r['N']} presence={r['presence']} discarded={r['discarded']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Teacher labeling — design Section 7/5.3")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("label", help="mp4 inference -> /labels or sidecar")
    p.add_argument("mp4")
    p.add_argument("--h5")
    p.add_argument("--det-thr", type=float, default=0.5, dest="det_thr",
                   help="Detection threshold (relaxed for S04 session — design Section 7)")
    p.add_argument("--device", default="auto", choices=("auto", "cuda", "cpu"))
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_label)
    p = sub.add_parser("qa", help="Manual review HTML gallery")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--h5")
    src.add_argument("--npz")
    p.add_argument("--mp4", help="Default: path from /labels attrs")
    p.add_argument("--sample", type=int, default=200)
    p.add_argument("--all", action="store_true", help="All frames (full review for S04)")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_qa)
    p = sub.add_parser("gate", help="Sum verdict JSONs -> Section 7 <2%% gate")
    p.add_argument("verdicts", nargs="+")
    p.set_defaults(fn=cmd_gate)
    p = sub.add_parser("pam", help="Apply QA -> /samples/Y·presence·label_ok final")
    p.add_argument("--h5", required=True)
    p.add_argument("--verdicts")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_pam)
    a = ap.parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
