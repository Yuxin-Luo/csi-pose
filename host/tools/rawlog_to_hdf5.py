#!/usr/bin/env python3
"""Rebuild session HDF5 from 3 rawlogs (rx0/1/2) (design Section 5 — raw log is the ground truth).

  python3 rawlog_to_hdf5.py --rx0 "/tmp/csilogs/rx0-*.rawlog" --rx1 "..." --rx2 "..." \
      --out ../sessions/soak-20260610.h5 --session soak-20260610
"""
import argparse
import glob
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from csi_pipe.rebuild import rebuild_session  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    for i in (0, 1, 2):
        ap.add_argument(f"--rx{i}", default=None, help=f"rx{i} rawlog glob pattern")
    ap.add_argument("--out", required=True)
    ap.add_argument("--session", default=None, help="Default: out filename stem")
    args = ap.parse_args()

    rx_paths = {}
    for i in (0, 1, 2):
        pat = getattr(args, f"rx{i}")
        if pat:
            # Alphabetical = chronological: bridge's 0-padded rx{id}-%Y%m%d-%H%M%S filename convention (unwrap order dependency)
            m = sorted(glob.glob(pat))
            if not m:
                sys.exit(f"No match: --rx{i} {pat}")
            rx_paths[i] = m
    if not rx_paths:
        sys.exit("--rx0/--rx1/--rx2 requires at least 1")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    stats = rebuild_session(rx_paths, out, session=args.session or out.stem,
                            progress=lambda s: print(s, flush=True))
    for rx, s in sorted(stats.items()):
        print(f"rx{rx}: frames={s['frames']} crc={s['crc']} mismatch={s['mismatch']}")
    # Saved
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
