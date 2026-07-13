#!/usr/bin/env python3
"""my_split.py — minimal single-session chronological split.

Splits /samples/* datasets by time order into front (train) + back (val) with
a gap in between. Skips /labels/* (which may be mp4-frame-indexed, not
sample-row-indexed) — training only consumes /samples/* anyway.

Usage:
  python3 train/my_split.py --h5 data/test/SESSION.h5 --out-dir data/processed/s01-r1
"""
import argparse
import sys
from pathlib import Path

import h5py
import numpy as np


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--h5", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--frac", type=float, default=0.8,
                    help="train fraction (front of session)")
    ap.add_argument("--gap-s", type=float, default=3.0)
    args = ap.parse_args(argv)

    src = Path(args.h5)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with h5py.File(src, "r") as h:
        # /samples/t_ns is the canonical row index — samples already ordered by t_ns
        t_ns = h["samples/t_ns"][...]
        n_total = len(t_ns)
        idx_cut = int(n_total * args.frac)
        cut_t = int(t_ns[idx_cut])
        gap_ns = int(args.gap_s * 1e9)
        train_mask = (np.arange(n_total) < idx_cut) & (t_ns < cut_t - gap_ns)
        val_mask = (np.arange(n_total) >= idx_cut) & (t_ns >= cut_t + gap_ns)

        # Sanity
        n_train = int(train_mask.sum())
        n_val = int(val_mask.sum())
        n_gap = n_total - n_train - n_val
        if n_train == 0 or n_val == 0:
            raise SystemExit(f"Split gives empty side: train={n_train}, val={n_val}")
        print(f"Split: total={n_total} train={n_train} val={n_val} gap={n_gap} "
              f"(cut_t={cut_t} gap_s={args.gap_s})")

        for role, mask in (("train", train_mask), ("val", val_mask)):
            dst = out_dir / f"{src.stem}-{role}.h5"
            with h5py.File(dst, "w") as ho:
                # Copy /samples/* row-aligned
                if "samples" in h:
                    si = h["samples"]
                    so = ho.create_group("samples")
                    for k, v in si.attrs.items():
                        so.attrs[k] = v
                    for name, ds in si.items():
                        if isinstance(ds, h5py.Dataset):
                            so.create_dataset(name, data=ds[...][mask])
                    # Patch F attr to reflect row count
                    if "F" in so.attrs:
                        so.attrs["F"] = int(mask.sum())
                # Copy /labels group (attrs only — needed by load_session for W/H)
                # /labels/* datasets may be mp4-frame-indexed, NOT row-aligned to /samples;
                # training doesn't consume them, but their attrs are required.
                if "labels" in h:
                    li = h["labels"]
                    lo = ho.create_group("labels")
                    for k, v in li.attrs.items():
                        lo.attrs[k] = v
                # Metadata only — skip /links/, /video/, /grid/
                ho.attrs["split_source"] = str(src)
                ho.attrs["split_role"] = role
                ho.attrs["split_frac"] = args.frac
                ho.attrs["split_gap_s"] = args.gap_s
                ho.attrs["split_cut_t_ns"] = cut_t
                ho.attrs["note"] = ("my_split.py: samples-row-aligned split + labels-attrs. "
                                    "/labels/* datasets skipped (mp4-frame-indexed, not consumed by training).")
            print(f"  {role}: {dst}  rows={int(mask.sum())}")


if __name__ == "__main__":
    sys.exit(main())