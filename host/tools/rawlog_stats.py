#!/usr/bin/env python3
"""Rawlog playback -> frame count / per-link seq gap / CRC summary (HDF5 rebuild path pre-verification).

  python rawlog_stats.py logs/rx0-*.rawlog
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from csi_host.framing import StreamParser  # noqa: E402
from csi_host.gap import LinkTracker       # noqa: E402
from csi_host.unwrap import TimeUnwrapper  # noqa: E402


def summarize(path):
    from csi_host.rawlog import read_rawlog
    parser = StreamParser()
    links = {}
    unwrap = TimeUnwrapper()
    frames = rawframes = texts = 0
    t_first = t_last = None
    for t_ns, chunk in read_rawlog(path):
        t_first = t_ns if t_first is None else t_first
        t_last = t_ns
        for kind, val in parser.feed(chunk):
            if kind == "frame":
                frames += 1
                _, ev = unwrap.update(boot_id=val.boot_id, t_us=val.esp_timer_us)
                if ev == "reboot":
                    for tr in links.values():
                        tr.rebaseline()
                links.setdefault((val.rx_id, val.tx_idx), LinkTracker()).update(val.seq)
            elif kind == "rawframe":
                rawframes += 1
            elif kind == "text":
                texts += 1

    span = (t_last - t_first) / 1e9 if frames and t_last and t_first else 0.0
    print(f"== {path}")
    print(f"  span={span:.1f}s frames={frames} rawframes={rawframes} texts={texts} "
          f"crc_err={parser.crc_errors} junk={parser.junk_bytes}B "
          f"wraps={unwrap.wraps} reboots={unwrap.reboots}")
    for (rx, tx), tr in sorted(links.items()):
        pps = tr.received / span if span > 0 else 0.0
        print(f"  rx{rx}-tx{tx}: rx={tr.received} lost={tr.lost} loss={tr.loss_ratio:.3%} "
              f"resets={tr.resets} avg_pps={pps:.1f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="rawlog path (wildcards allowed -- PowerShell not expanded)")
    args = ap.parse_args()
    import glob
    expanded = []
    for p in args.paths:
        matches = sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p]
        if not matches:
            print(f"Warning: no match -- {p}", file=sys.stderr)
        expanded.extend(matches)
    if not expanded:
        sys.exit(1)
    for p in expanded:
        summarize(Path(p))


if __name__ == "__main__":
    main()
