#!/usr/bin/env python3
"""M1.5 mini-capture operator logger — record segment boundaries.

  python m15_protocol.py --capture 1 --session m15-cap1 --out logs\\m15-cap1-segments.json

Empty Enter = settle (start) / move (end) toggle, u+Enter = cancel previous boundary, q+Enter = abort.
Recommended times are just guides — keys are the source of truth. No real-time counter (actual output when end boundary is confirmed).
Run on the same machine as the recorder (time.time_ns uses the same epoch — no conversion assumed for joining)."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from csi_pipe.m15_protocol import M15Session  # noqa: E402


def _say(seg):
    if seg["empty"]:
        return "Activity area cleared — subject left (mat stays)"
    if seg["posture"] == "stand":
        return f"Stand at position {seg['pos']} — rotate queue every ~10s during session (N->E->S->W)"
    return {"sit": "Sit on mat (position 5)",
            "lie": "Lie on mat (position 5) — head direction is the same for both captures"}[seg["posture"]]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture", type=int, required=True, choices=(1, 2),
                    help="Capture round — 1=sequential tour, 2=shuffled tour (positions are different)")
    ap.add_argument("--session", required=True, help="Session label (same as recorder --session)")
    ap.add_argument("--out", required=True, help="segments.json output path")
    a = ap.parse_args(argv)
    sess = M15Session(a.capture, a.session)
    print(f"[m15] Capture {a.capture} 13 segments — Enter=start/end toggle, u=cancel previous boundary, q=abort")
    while not sess.done:
        seg = sess.current()
        state = "In progress -> Enter with movement instruction" if sess.settled \
            else "Waiting -> Confirm subject settled then Enter"
        print(f"\n[{seg['idx'] + 1}/13] {_say(seg)} (recommended {seg['hint_s']}s) — {state}")
        try:
            cmd = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()                                   # ^C newline cleanup
            cmd = "q"
        if cmd == "":
            try:
                if sess.mark(time.time_ns()) == "end":
                    s, e = sess.bounds[-1]
                    print(f"  Segment {seg['idx'] + 1}/13 confirmed: {(e - s) / 1e9:.1f}s")
                else:
                    print(f"  ▶▶▶ Segment {seg['idx'] + 1}/13 started — recording! (Enter again to end)")
            except ValueError as e:
                print(f"  Ignored: {e}")
        elif cmd == "u":
            print(f"  undo: {sess.undo() or 'no boundary to cancel'}")
        elif cmd == "q":
            sess.abort()
            print("  Aborted — only confirmed segments recorded")
            break
        else:
            print("  Ignored — only Enter/u/q are recognized")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(sess.result(), ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"[m15] Saved: {out} (segments={len(sess.bounds)} aborted={sess.aborted})")
    return 1 if sess.aborted else 0


if __name__ == "__main__":
    sys.exit(main())
