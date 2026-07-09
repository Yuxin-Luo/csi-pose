#!/usr/bin/env python3
"""Overnight soak rawlog -> M0 verdict.

  python3 soak_report.py /tmp/csilogs/*.rawlog --window 22:00-07:00 --json verdict.json

Since 9P is slow, using rawlog copy in /tmp is recommended (analysis tip).
"""
import argparse
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from csi_pipe.soak import analyze_soak, render_report  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="rawlog path (wildcards allowed)")
    ap.add_argument("--window", default=None, help="Unattended interval KST, e.g.: 22:00-07:00")
    ap.add_argument("--fit-window", type=float, default=600.0, help="Clock fit window (seconds)")
    ap.add_argument("--json", default=None, help="Verdict JSON save path")
    args = ap.parse_args()

    expanded = []
    for p in args.paths:
        m = sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p]
        if not m:
            print(f"Warning: no match — {p}", file=sys.stderr)
        expanded.extend(m)
    if not expanded:
        sys.exit(1)

    rep = analyze_soak(expanded, window=args.window, fit_window_s=args.fit_window)
    print(render_report(rep))
    if args.json:
        Path(args.json).write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
        print(f"JSON saved: {args.json}")


if __name__ == "__main__":
    main()
