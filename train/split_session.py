#!/usr/bin/env python3
# Session h5 chronological physical split — single session train/val.
# Row-aligned datasets in samples/·labels/ are sliced by t_ns order into front frac(train)/back 1-frac(val).
# Boundary gap (gap-s — window span ~2.8s overlap leak blocking) rows excluded from train (val preservation priority).
# Non-learning groups like grid/links/video not copied (csi_train.data.load_session not used).
# Example: python3 train/split_session.py --h5 host/sessions/SESSION-YYYYMMDD-HHMMSS.h5 --out-dir ~/data
import argparse
import sys
from pathlib import Path

import h5py
import numpy as np

ROW_GROUPS = ("samples", "labels")     # Only copy row-aligned groups — rest are non-learning


def split_indices(t_ns, frac, gap_ns):
    # t_ns (monotonic) -> (train bool, val bool, cut_t). Gap rows are False for both.
    t = t_ns.astype(np.int64)
    if np.any(np.diff(t) < 0):
        # samples/t_ns non-monotonic — recorder convention violation, cannot split
        raise SystemExit("samples/t_ns non-monotonic -- recorder convention violation, cannot split")
    idx_cut = int(len(t) * frac)
    if idx_cut <= 0 or idx_cut >= len(t):
        # Invalid split boundary
        raise SystemExit(f"Invalid split boundary (idx_cut={idx_cut}, N={len(t)}) -- check frac")
    cut_t = int(t[idx_cut])
    val = np.zeros(len(t), bool)
    val[idx_cut:] = True
    train = (~val) & (t < cut_t - int(gap_ns))
    if not train.any():
        # train 0 rows (gap too large or session too small)
        raise SystemExit("train 0 rows (gap too large or session too small)")
    return train, val, cut_t


def write_split(src, dst, rows, *, role, frac, gap_s, cut_t_ns):
    # Slice src row-aligned groups (ROW_GROUPS) by rows mask and write to dst.
    with h5py.File(src, "r") as hi, h5py.File(dst, "w") as ho:
        for k, v in hi.attrs.items():
            ho.attrs[k] = v
        n = int(rows.sum())
        for gname in ROW_GROUPS:
            if gname not in hi:
                continue
            gi, go = hi[gname], ho.create_group(gname)
            for k, v in gi.attrs.items():
                go.attrs[k] = v
            if "F" in go.attrs:
                go.attrs["F"] = n              # row count attr corrected after slice
            for name, ds in gi.items():
                if not isinstance(ds, h5py.Dataset) or ds.shape[0] != len(rows):
                    # Not row-aligned — cannot split
                    raise SystemExit(f"{gname}/{name}: not row-aligned -- cannot split")
                go.create_dataset(name, data=ds[...][rows])
        ho.attrs["split_source"] = str(src)
        ho.attrs["split_role"] = role
        ho.attrs["split_frac"] = frac
        ho.attrs["split_gap_s"] = gap_s
        ho.attrs["split_cut_t_ns"] = cut_t_ns
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--h5", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--frac", type=float, default=0.8)
    ap.add_argument("--gap-s", type=float, default=3.0)
    a = ap.parse_args(argv)
    src = Path(a.h5)
    if not src.exists():
        raise SystemExit(f"Input h5 not found: {src}")
    out_dir = Path(a.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    with h5py.File(src, "r") as h:
        t_ns = h["samples/t_ns"][...]
    train, val, cut_t = split_indices(t_ns, a.frac, int(a.gap_s * 1e9))
    total = 0
    for role, rows in (("train", train), ("val", val)):
        dst = out_dir / f"{src.stem}-{role}.h5"
        n = write_split(src, dst, rows, role=role, frac=a.frac,
                        gap_s=a.gap_s, cut_t_ns=cut_t)
        total += n
        print(f"{role}: {dst}  rows={n}")
    print(f"Gap exclusion {len(t_ns) - total} rows / total {len(t_ns)} rows (cut_t_ns={cut_t})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
