#!/usr/bin/env python3
"""M1.5 probe gate CLI — cross-session go/no-go (m15-probe).

Example: python3 train/probe.py \
        --train-h5 host/sessions/CAP1-….h5 \
        --train-seg host/logs/CAP1-segments.json \
        --eval-h5  host/sessions/CAP2-….h5 \
        --eval-seg host/logs/CAP2-segments.json \
        --out logs/m15_verdict.json

Gate = max(linear, mlp) >= threshold (pos9 0.85 / posture3 0.90 / lying_empty 0.95).
FAIL verdict is not a tool failure — exit 0 when done, non-zero only for input errors (SystemExit).
Input ~35MB loaded once — /mnt/c direct read allowed (ext4 rule §15 applies to epoch-repeat I/O)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from csi_train.probe import run_probe  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train-h5", required=True)
    ap.add_argument("--train-seg", required=True)
    ap.add_argument("--eval-h5", required=True)
    ap.add_argument("--eval-seg", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--trim-s", type=float, default=2.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--with-phase", action="store_true",
                    help="Combine X_phase 5040 features — for M2.5 diagnosis (not meaningful for gate in default mode)")
    a = ap.parse_args(argv)
    v = run_probe(a.train_h5, a.train_seg, a.eval_h5, a.eval_seg,
                  trim_s=a.trim_s, seed=a.seed, epochs=a.epochs,
                  with_phase=a.with_phase)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(v, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n== §13 M1.5 gate: {v['train_session']} train -> {v['eval_session']} eval ==")
    for name, t in v["tasks"].items():
        print(f"{name:12s} gate={t['gate_acc']:.3f}({t['gate_model']}) "
              f"thr={t['threshold']:.2f} -> {'PASS' if t['pass'] else 'FAIL'}"
              f"  [linear {t['linear_acc']:.3f} / mlp {t['mlp_acc']:.3f}"
              f" / same-session ref {max(t['same_session_ref'].values()):.3f}]")
    print("overall:", "PASS — full collection can proceed" if v["overall_pass"] else
          "FAIL — §13 planned action (pos9=40cm reposition+re-run / lying=height mismatch recheck)")
    print(f"verdict: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
