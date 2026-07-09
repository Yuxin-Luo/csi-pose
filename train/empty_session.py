#!/usr/bin/env python3
"""M15 segment capture -> presence=0 negative training session.

Cuts only intervals from segment JSON (empty=true) as [start+trim, end-trim),
producing a new h5 in the form required by csi_train.data.load_session
(samples/X·Y·presence·label_ok·t_ns·valid + labels attrs W·H·F).
Y is PAM convention (4,18,18) zeros — presence=0 rows have loss w=1[presence]·...=0 so
coordinate terms are unused (Section 8.2).
Trim convention matches probe join (csi_train.probe.task_rows).

Example: python3 train/empty_session.py \
        --h5 host/sessions/CAP-YYYYMMDD-HHMMSS.h5 \
        --segments host/logs/CAP-segments.json \
        --out ~/data/CAP-empty.h5
"""
import argparse
import json
import sys
from pathlib import Path

import h5py
import numpy as np

Y_PAM_SHAPE = (4, 18, 18)              # PAM builder output convention (verified with teacher-conn-01 real data)


def build_empty_session(h5_path, segdoc_path, out_path, *, trim_s, wh):
    """Cut only empty segment rows to produce negative session h5 -> returns row count."""
    segdoc = json.loads(Path(segdoc_path).read_text(encoding="utf-8"))
    if segdoc.get("aborted"):
        raise SystemExit(f"{segdoc_path}: aborted segment record — not usable")
    empties = [s for s in segdoc["segments"] if s.get("empty")]
    if not empties:
        raise SystemExit(f"{segdoc_path}: no empty segments")

    trim_ns = int(trim_s * 1e9)
    with h5py.File(h5_path, "r") as hi:
        X = hi["samples/X"]
        if X.shape[1:] != (280, 3, 3):
            raise SystemExit(f"{h5_path}: X shape {X.shape[1:]} != (280,3,3) — Section 6.1 convention violation")
        t = hi["samples/t_ns"][...].astype(np.int64)
        rows = np.zeros(len(t), bool)
        for s in empties:
            rows |= (t >= s["t_start_ns"] + trim_ns) & (t < s["t_end_ns"] - trim_ns)
        n = int(rows.sum())
        if n == 0:
            raise SystemExit("0 rows after trim — check segment length and trim-s")
        with h5py.File(out_path, "w") as ho:
            g = ho.create_group("samples")
            g.create_dataset("X", data=X[...][rows])
            g.create_dataset("t_ns", data=hi["samples/t_ns"][...][rows])
            g.create_dataset("valid", data=hi["samples/valid"][...][rows])
            g.create_dataset("Y", data=np.zeros((n, *Y_PAM_SHAPE), np.float16))
            g.create_dataset("presence", data=np.zeros(n, bool))
            g.create_dataset("label_ok", data=np.ones(n, bool))
            lab = ho.create_group("labels")     # load_session requires W·H attrs
            lab.attrs["W"], lab.attrs["H"] = int(wh[0]), int(wh[1])
            lab.attrs["F"] = n
            lab.attrs["source"] = "empty_session (unlabeled negative — segment empty intervals)"
            ho.attrs["empty_source"] = str(h5_path)
            ho.attrs["empty_segdoc"] = str(segdoc_path)
            ho.attrs["empty_seg_idx"] = [int(s["idx"]) for s in empties]
            ho.attrs["empty_trim_s"] = trim_s
            ho.attrs["plan_version"] = segdoc.get("plan_version", "")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--h5", required=True)
    ap.add_argument("--segments", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--trim-s", type=float, default=2.0)
    ap.add_argument("--wh", type=int, nargs=2, default=(1280, 720),
                    help="labels attrs W H (match campaign camera resolution)")
    a = ap.parse_args(argv)
    for p, lbl in ((a.h5, "input h5"), (a.segments, "segment JSON")):
        if not Path(p).exists():
            raise SystemExit(f"{lbl} not found: {p}")
    out = Path(a.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    n = build_empty_session(a.h5, a.segments, out, trim_s=a.trim_s, wh=tuple(a.wh))
    print(f"Done: {out}  rows={n} (all presence=0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
