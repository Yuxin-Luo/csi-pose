#!/usr/bin/env python3
# M2.5 ablation runner — M2 best loss mode inheritance {phase, rssi, rssi_phase} 3 runs.
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_ablation as ra  # noqa: E402

SUFFIX_RUNS = [("phase", ["--phase"]), ("rssi", ["--rssi"]),
               ("rssi_phase", ["--rssi", "--phase"])]
PRIORITY = ("amp", "rssi", "phase", "rssi_phase")   # On tie, simpler config prioritized (spec Section 3)


def load_m2(path):
    # ablation-summary.json -> {"best","pck02","gate_baseline"} | error string.
    p = Path(path)
    if not p.exists():
        return f"M2 summary not found: {p} -- run_ablation.py must be run first"
    try:
        s = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return f"M2 summary corrupted: {p} ({e})"
    best = s.get("best_run")
    if not best:
        return f"M2 best_run not found (all runs failed?): {p}"
    entry = next((r for r in s.get("runs", []) if r.get("name") == best), None)
    if entry is None or entry.get("pck02") is None:
        return f"M2 best_run({best}) has no pck02 record: {p}"
    return {"best": best, "pck02": float(entry["pck02"]),
            "gate_baseline": s.get("gate_baseline_pck02")}


def derive_runs(best):
    # 3 runs inheriting M2 best run's fit parameters — returns None if best not in RUNS.
    base = next((r for r in ra.RUNS if r["name"] == best), None)
    if base is None:
        return None
    return [{"name": f"{best}_{suf}", "extra": base["extra"] + extra}
            for suf, extra in SUFFIX_RUNS]


def final_config(m2_pck02, judged):
    # {amp, rssi, phase, rssi_phase} pck02 argmax — on tie, PRIORITY earlier (> comparison = first-kept).
    cand = {"amp": m2_pck02}
    cand.update({suf: j["pck02"] for (suf, _), j in zip(SUFFIX_RUNS, judged)})
    best_cfg = best_p = None
    for cfg in PRIORITY:
        p = cand.get(cfg)
        if p is not None and (best_p is None or p > best_p):
            best_cfg, best_p = cfg, p
    return best_cfg, best_p


def main(argv=None):
    ap = argparse.ArgumentParser(description="M2.5 ablation runner (spec: "
                                 "docs/superpowers/specs/2026-06-12-m25-phase-design.md)")
    ap.add_argument("--config", default="configs/train.yaml")
    ap.add_argument("--out-root", default="runs")
    ap.add_argument("--m2-summary", default=None,
                    help="Default: <out-root>/ablation-summary.json")
    ap.add_argument("--epochs", type=int, default=None, help="Fit pass-through for quick smoke test")
    ap.add_argument("--device", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    config = Path(args.config).resolve()
    out_root = Path(args.out_root).resolve()
    m2_path = Path(args.m2_summary) if args.m2_summary else out_root / "ablation-summary.json"

    errs = ra.preflight(config, args.device, out_root)
    runs = None
    m2 = load_m2(m2_path)
    if isinstance(m2, str):
        errs.append(m2)
    else:
        runs = derive_runs(m2["best"])
        if runs is None:
            errs.append(f"M2 best_run({m2['best']}) not in RUNS list -- "
                        "run_ablation version mismatch suspected")
    if errs:
        for e in errs:
            print(f"Preflight failure: {e}", file=sys.stderr)
        return 2

    if args.dry_run:
        for run in runs:
            step = ra.plan_step(out_root, run["name"])
            if step == "skip":
                print(f"{run['name']}: skip (report exists)")
                continue
            if step == "full":
                print(f"{run['name']} fit:", " ".join(
                    ra.build_fit_cmd(config, out_root, run, args.epochs, args.device)))
            print(f"{run['name']} eval:", " ".join(
                ra.build_eval_cmd(config, out_root, run["name"], args.device)))
        return 0

    (out_root / "ablation-logs").mkdir(parents=True, exist_ok=True)
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    judged = []
    for run in runs:
        status = ra.run_one(run, config, out_root, args.epochs, args.device)
        report = ra._read_report(out_root, run["name"])
        judged.append({"name": run["name"], "status": status,
                       "log": str(out_root / "ablation-logs" / f"{run['name']}.log"),
                       **ra.judge_run(report, m2["gate_baseline"])})
    for j in judged:
        j["delta_pck02"] = None if j["pck02"] is None else j["pck02"] - m2["pck02"]
    cfg_name, cfg_pck = final_config(m2["pck02"], judged)
    overall = ra.summarize(judged)

    summary = {"config": str(config),
               "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
               "started": started, "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "argv": list(argv) if argv is not None else sys.argv[1:],
               "m2_summary": str(m2_path),
               "m2_best": {"name": m2["best"], "pck02": m2["pck02"]},
               "gate_baseline_pck02": m2["gate_baseline"],
               "runs": judged, "final_config": cfg_name,
               "final_config_pck02": cfg_pck, **overall}
    (out_root / "m25-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    ra.print_table(judged, overall, m2["gate_baseline"])
    print("(M2 gate row above = M2.5 variant 3-run Section 13 gate)")
    print(f"M2.5 final config: {cfg_name} (pck02 {cfg_pck:.3f} / "
          f"M2 best {m2['best']} {m2['pck02']:.3f}) -- Section 12 entry is human")
    return 1 if any(j["status"] == "error" for j in judged) else 0


if __name__ == "__main__":
    sys.exit(main())
