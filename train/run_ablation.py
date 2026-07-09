#!/usr/bin/env python3
# M2 ablation overnight batch runner — baselines -> fit x4 -> eval x4 -> Section 13 gate summary.
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # train/ → csi_train
from csi_train.data import load_manifest  # noqa: E402

# Gate thresholds — train/README "gate judgment"·M2 spec Section 13
GATE_ABS = 0.35        # val PCK@0.2 absolute lower bound
GATE_MARGIN = 0.10     # required margin vs gate_baseline_pck02
GATE_LYING = 0.25      # lying PCK@0.2 lower bound
KAPPA_WARN_LT = 0.4    # if kappa < this value, lying denominator correction suspected invalid (Section 6) — warning only, not failure

TRAIN_SCRIPT = Path(__file__).resolve().parent / "train.py"

RUNS = [  # §8.3 fixed 3 variants + augmentation 1 run — list order is best_run tiebreaker priority
    {"name": "pam_full", "extra": ["--loss-mode", "pam_full"]},
    {"name": "diag_balanced", "extra": ["--loss-mode", "diag_balanced"]},
    {"name": "diag_only_vec", "extra": ["--loss-mode", "diag_only", "--vector-head"]},
    # Small-sample augmentation ablation (docs/research-20260612-low-data-techniques.md) — no-augment prioritized on tie
    {"name": "pam_full_aug", "extra": ["--loss-mode", "pam_full", "--augment"]},
]


def judge_run(report, gate_baseline):
    # report-val.json dict (None if absent) + baseline -> run judgment dict.
    # Gate 3-value logic: abs/lying failure or error run = certain False regardless of margin,
    # margin only unknown (gate_baseline None) = None (indeterminate), all satisfied = True.
    # Lying unmeasured (pck_lying["0.2"] == null): lying02=None, lying_ok=None.
    # abs/margin certain False stays False; abs·margin pass but unknown -> gate=None (indeterminate).
    if report is None:
        return {"pck02": None, "lying02": None, "kappa": None,
                "abs_ok": None, "margin_ok": None, "lying_ok": None,
                "gate": False, "kappa_warn": False}
    pck02 = float(report["pck"]["0.2"])
    lying_raw = report["pck_lying"]["0.2"]
    lying02 = None if lying_raw is None else float(lying_raw)
    kappa = float(report["kappa"])
    abs_ok = pck02 >= GATE_ABS
    lying_ok = None if lying02 is None else lying02 >= GATE_LYING
    margin_ok = None if gate_baseline is None else pck02 >= gate_baseline + GATE_MARGIN
    if not abs_ok or lying_ok is False or margin_ok is False:
        gate = False
    elif lying_ok is None or margin_ok is None:
        gate = None
    else:
        gate = True
    return {"pck02": pck02, "lying02": lying02, "kappa": kappa,
            "abs_ok": abs_ok, "margin_ok": margin_ok, "lying_ok": lying_ok,
            "gate": gate, "kappa_warn": kappa < KAPPA_WARN_LT}


def summarize(judged):
    # judge_run result list (each entry has 'name' key, RUNS order) -> best_run·m2_gate.
    # best = highest pck02 (tiebreaker = earlier in list — max keeps first max). Excludes runs without report.
    # All excluded: best_run=None·m2_gate=False.
    with_report = [j for j in judged if j["pck02"] is not None]
    if not with_report:
        return {"best_run": None, "m2_gate": False}
    best = max(with_report, key=lambda j: j["pck02"])
    return {"best_run": best["name"], "m2_gate": best["gate"]}


def plan_step(out_root, name):
    # Idempotent re-run judgment — spec Section 4. Partial best.pt is not completion proof (based on fit-done marker).
    if _read_report(out_root, name) is not None:   # Corrupted report treated as incomplete -> re-evaluate self-recovery
        return "skip"
    if (out_root / "ablation-logs" / f"{name}.fit-done").exists():
        return "eval_only"
    return "full"


def _common(config, device):
    return ["--config", str(config)] + (["--device", device] if device is not None else [])


def build_baselines_cmd(config, out_root, device):
    return [sys.executable, str(TRAIN_SCRIPT), "baselines",
            *_common(config, device), "--out-root", str(out_root)]


def build_fit_cmd(config, out_root, run, epochs, device):
    # --name is always explicit — do not rely on train.py auto-naming for out_root/<name> path contract.
    cmd = [sys.executable, str(TRAIN_SCRIPT), "fit", *_common(config, device),
           "--out-root", str(out_root), *run["extra"]]
    if epochs is not None:
        cmd += ["--epochs", str(epochs)]
    return cmd + ["--name", run["name"]]


def build_eval_cmd(config, out_root, name, device):
    # --split omitted = 'val' default fixed (report-val.json spec, spec Section 5)
    return [sys.executable, str(TRAIN_SCRIPT), "eval", *_common(config, device),
            "--ckpt", str(out_root / name / "best.pt")]


def _cuda_available():
    import torch
    return torch.cuda.is_available()


def preflight(config, device, out_root):
    # Pre-flight full inspection — list of error strings (empty list = pass). Spec Section 3-1.
    if not config.exists():
        # Config file not found
        return [f"Config file not found: {config}"]
    errors = []
    try:
        man = load_manifest(config)
    except Exception as e:                      # yaml corrupted/empty -> clean error
        # Config file parsing failed
        return [f"Config file parsing failed: {config} -- {type(e).__name__}: {e}"]
    sessions = man.get("sessions") or []
    if not sessions:
        # Manifest sessions empty
        errors.append(f"Manifest sessions empty: {config}")
    for s in sessions:
        h5 = Path(s["h5"])
        if str(h5).startswith("/mnt/"):
            # /mnt/ path learning forbidden (9P, design Section 15) — copy to ext4
            errors.append(f"/mnt/ path learning forbidden (9P, design §15) -- copy to ext4: {h5}")
        elif not h5.exists():
            # Session HDF5 not found
            errors.append(f"Session HDF5 not found: {h5}")
    if device != "cpu" and not _cuda_available():
        # CUDA unavailable — overnight run meaningless with train.py cpu implicit fallback. To force CPU use --device cpu explicitly
        errors.append("CUDA unavailable -- overnight run meaningless with cpu implicit fallback. "
                      "To force CPU use --device cpu explicitly")
    try:
        out_root.mkdir(parents=True, exist_ok=True)
        probe = out_root / ".write-probe"
        probe.write_text("")
        probe.unlink()
    except OSError as e:
        # out-root not writable
        errors.append(f"out-root not writable: {out_root} ({e})")
    return errors


def _read_report(out_root, name):
    p = out_root / name / "report-val.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):    # Partial record (power loss etc.) -> downgraded to absent
        return None


def _log_header(logf, label, cmd):
    logf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {label}: {' '.join(cmd)}\n")
    logf.flush()


def _run_logged(cmd, logf, label):
    _log_header(logf, label, cmd)
    return subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT).returncode == 0


def run_one(run, config, out_root, epochs, device):
    # One run's fit->eval — idempotent skip, fit-done marker, log. Returns: done|skipped|error.
    name = run["name"]
    step = plan_step(out_root, name)
    if step == "skip":
        return "skipped"
    logs = out_root / "ablation-logs"
    with open(logs / f"{name}.log", "a", encoding="utf-8") as logf:
        if step == "full":
            if not _run_logged(build_fit_cmd(config, out_root, run, epochs, device),
                               logf, "fit"):
                return "error"
            (logs / f"{name}.fit-done").write_text("")
        if not _run_logged(build_eval_cmd(config, out_root, name, device),
                           logf, "eval"):
            return "error"
    return "done"


def print_plan(config, out_root, args):
    # --dry-run: output only the command sequence and skip judgments that would be executed.
    baselines_path = out_root / "baselines.json"
    if args.force_baselines or (not baselines_path.exists() and not args.skip_baselines):  # Same condition as main need_baselines (sync maintained)
        print("baselines:", " ".join(build_baselines_cmd(config, out_root, args.device)))
    else:
        reason = "--skip-baselines" if args.skip_baselines else "reuse existing"
        print(f"baselines: skip ({reason})")
    for run in RUNS:
        step = plan_step(out_root, run["name"])
        if step == "skip":
            print(f"{run['name']}: skip (report exists)")
            continue
        if step == "full":
            print(f"{run['name']} fit:",
                  " ".join(build_fit_cmd(config, out_root, run, args.epochs, args.device)))
        print(f"{run['name']} eval:",
              " ".join(build_eval_cmd(config, out_root, run["name"], args.device)))


def print_table(judged, overall, gate_baseline):
    # Console table for morning check — same content as summary.
    def b(v):
        return "—" if v is None else ("✓" if v else "✗")

    print(f"\n{'run':<16}{'status':<9}{'pck@0.2':<9}{'lying':<8}{'κ':<8}"
          f"{'abs':<5}{'margin':<8}{'ly_ok':<7}GATE")
    for j in judged:
        pck = "—" if j["pck02"] is None else f"{j['pck02']:.3f}"
        ly = "—" if j["lying02"] is None else f"{j['lying02']:.3f}"
        ka = "—" if j["kappa"] is None else (
            f"{j['kappa']:.2f}" + ("⚠" if j["kappa_warn"] else ""))
        gate = {True: "PASS", False: "FAIL", None: "N/A"}[j["gate"]]
        print(f"{j['name']:<16}{j['status']:<9}{pck:<9}{ly:<8}{ka:<8}"
              f"{b(j['abs_ok']):<5}{b(j['margin_ok']):<8}{b(j['lying_ok']):<7}{gate}")
    base = "—" if gate_baseline is None else f"{gate_baseline:.3f}"
    # Gate None reason: if baselines exist, margin_ok cannot be None -> remaining cause is lying unmeasured
    reason = "no baselines" if gate_baseline is None else "lying unmeasured"
    m2 = {True: "PASS", False: "FAIL", None: f"inconclusive ({reason})"}[overall["m2_gate"]]
    print(f"\nM2 gate: {m2}  (best={overall['best_run']}, baseline {base}+{GATE_MARGIN})")


def main(argv=None):
    ap = argparse.ArgumentParser(description="M2 ablation overnight batch runner (spec: "
                                 "docs/superpowers/specs/2026-06-11-ablation-runner-design.md)")
    ap.add_argument("--config", default="configs/train.yaml")
    ap.add_argument("--out-root", default="runs")
    ap.add_argument("--epochs", type=int, default=None, help="Fit pass-through for quick smoke test")
    ap.add_argument("--device", default=None)
    ap.add_argument("--dry-run", action="store_true")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--skip-baselines", action="store_true")
    g.add_argument("--force-baselines", action="store_true")
    args = ap.parse_args(argv)
    config = Path(args.config).resolve()
    out_root = Path(args.out_root).resolve()
    logs = out_root / "ablation-logs"

    errs = preflight(config, args.device, out_root)
    if errs:
        for e in errs:
            # Preflight failure
            print(f"Preflight failure: {e}", file=sys.stderr)
        return 2
    if args.dry_run:
        print_plan(config, out_root, args)
        return 0
    logs.mkdir(parents=True, exist_ok=True)
    started = time.strftime("%Y-%m-%dT%H:%M:%S")

    baselines_path = out_root / "baselines.json"
    need_baselines = args.force_baselines or (
        not baselines_path.exists() and not args.skip_baselines)
    if need_baselines:
        with open(logs / "baselines.log", "a", encoding="utf-8") as logf:
            if not _run_logged(build_baselines_cmd(config, out_root, args.device),
                               logf, "baselines"):
                # Baselines failed — abort. Overnight batch without margin baseline is meaningless
                print("baselines failed -- abort. Overnight batch without margin baseline is meaningless "
                      f"(log: {logs / 'baselines.log'})", file=sys.stderr)
                return 2
    elif (baselines_path.exists()
          and config.stat().st_mtime > baselines_path.stat().st_mtime):
        # Warning: config newer than baselines.json — risk of manifest mismatch
                print(f"Warning: {config.name} is newer than baselines.json -- risk of manifest mismatch "
                      "(README ⚠). consider --force-baselines", file=sys.stderr)

    gate_baseline = None
    if baselines_path.exists():
        try:
            gate_baseline = float(json.loads(
                baselines_path.read_text(encoding="utf-8"))["gate_baseline_pck02"])
        except (ValueError, KeyError, TypeError, OSError):
            # baselines.json corrupted — regeneration needed with --force-baselines
                print(f"baselines.json corrupted -- regeneration needed with --force-baselines: {baselines_path}",
                  file=sys.stderr)
            return 2

    judged = []
    for run in RUNS:
        status = run_one(run, config, out_root, args.epochs, args.device)
        report = _read_report(out_root, run["name"])
        judged.append({"name": run["name"], "status": status,
                       "log": str(logs / f"{run['name']}.log"),
                       **judge_run(report, gate_baseline)})
    overall = summarize(judged)

    summary = {"config": str(config),
               "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
               "started": started, "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "argv": list(argv) if argv is not None else sys.argv[1:],
               "gate_baseline_pck02": gate_baseline, "runs": judged, **overall}
    (out_root / "ablation-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print_table(judged, overall, gate_baseline)
    return 1 if any(j["status"] == "error" for j in judged) else 0


if __name__ == "__main__":
    sys.exit(main())
