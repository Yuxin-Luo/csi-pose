# Training/evaluation loop — AdamW, cosine+warmup, bf16, best=val PCK@0.2.
# Checkpoint stores only tensors and primitives -> loadable with torch.load default (weights_only=True).
# Loss parts are detached tensors (Task 4 review) — accumulated as tensors, scalar conversion only at epoch boundary.
# use_compile: disabled by default due to torch 2.12+cu130 inductor pre-existing defects in this environment.
import json
import math
import random
import subprocess
from pathlib import Path

import numpy as np
import torch

from .augment import augment_batch, mu0_from_stats
from .data import CHANNEL_CONVENTION, diag_pose
from .loss import pose_loss
from .model import WiSPPN
from .pck import calibrate_kappa, evaluate


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def make_scheduler(opt, *, total_steps, warmup_steps):
    def lr_lambda(step):
        if step < warmup_steps:
            return (step + 1) / max(1, warmup_steps)
        p = (step - warmup_steps) / max(1, total_steps - warmup_steps)
        return 0.5 * (1.0 + math.cos(math.pi * min(1.0, p)))
    return torch.optim.lr_scheduler.LambdaLR(opt, lr_lambda)


def predict(model, X, *, device, batch=64):
    # X (N,C,3,3) f16 numpy (C=280|560) -> (xy (N,18,2) f32, c_hat (N,18) f32) — diagonal extraction.
    # Default batch 64: forward upsamples input to 144x144 so (B,150,144,144) f32
    # intermediate tensor is B×11.9MB — at 512 that was 5.93GiB single allocation causing 16GiB GPU OOM (measured).
    model.eval()
    xs, cs = [], []
    with torch.no_grad():
        for s in range(0, len(X), batch):
            x = torch.from_numpy(np.ascontiguousarray(X[s:s + batch])).float().to(device)
            o = model(x)
            if o.dim() == 4:
                d = torch.arange(o.shape[-1], device=o.device)
                xy = torch.stack([o[:, 0, d, d], o[:, 1, d, d]], dim=2)
                ch = o[:, 2, d, d]
            else:
                xy = o[:, :2].transpose(1, 2)
                ch = o[:, 2]
            xs.append(xy.float().cpu().numpy())
            cs.append(ch.float().cpu().numpy())
    return np.concatenate(xs), np.concatenate(cs)


def _git_rev():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def save_ckpt(path, model, *, mu, sigma, config, kappa, best,
              mu_phase=None, sigma_phase=None):
    raw = getattr(model, "_orig_mod", model)               # torch.compile unwrap
    ck = {"state_dict": {k: v.cpu() for k, v in raw.state_dict().items()},
          "mu": torch.from_numpy(np.asarray(mu)),
          "sigma": torch.from_numpy(np.asarray(sigma)),
          "channel_convention": CHANNEL_CONVENTION,
          "config": config, "git_rev": _git_rev(),
          "kappa": float(kappa), "best": best}
    if mu_phase is not None:
        ck["mu_phase"] = torch.from_numpy(np.asarray(mu_phase))
        ck["sigma_phase"] = torch.from_numpy(np.asarray(sigma_phase))
    torch.save(ck, path)


def load_ckpt(path, *, device="cpu"):
    ck = torch.load(path, map_location=device)             # weights_only=True default (2.6+)
    cfg = ck["config"]
    model = WiSPPN(in_ch=cfg["in_ch"], vector_head=cfg["vector_head"]).to(device)
    model.load_state_dict(ck["state_dict"])
    model.eval()
    return model, ck


def _val_arrays(rows):
    m = rows.presence
    xy, c = diag_pose(rows.Y[m])
    return m, xy, c, rows.WH[m], rows.stype[m]


def train_model(splits, hyper, *, mode="pam_full", vector_head=False, lam=1.0,
                augment=False, name="run", out_root="runs", device=None,
                use_compile=False, progress=print):
    """Train -> {"best_pck02", "ckpt", "epochs"}. best = val PCK@0.2 max epoch."""
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    set_seed(hyper["seed"])
    tr, va = splits["train"], splits["val"]
    in_ch = tr.X.shape[1]
    model = WiSPPN(in_ch=in_ch, vector_head=vector_head).to(device)
    if use_compile:
        model = torch.compile(model)
    Xtr = torch.from_numpy(tr.X).to(device)                # f16 storage, batch casting
    Ytr = torch.from_numpy(tr.Y).to(device)
    Ptr = torch.from_numpy(tr.presence).to(device)
    B, epochs = hyper["batch"], hyper["epochs"]
    steps = math.ceil(len(tr.X) / B)
    opt = torch.optim.AdamW(model.parameters(), lr=hyper["lr"], weight_decay=hyper["wd"])
    sched = make_scheduler(opt, total_steps=steps * epochs,
                           warmup_steps=steps * hyper["warmup"])
    # κ — calibration from train upright GT, fallback 0.5 if no upright frames (§9)
    mtr, xy_tr, c_tr, WH_tr, _ = _val_arrays(tr)
    try:
        kappa = calibrate_kappa(xy_tr, c_tr, WH_tr)
    except ValueError:
        kappa = 0.5
        # kappa calibration failed (no upright frames) — fallback to 0.5
        progress("κ calibration failed (no upright frames) — fallback to 0.5")
    mva, xy_va, c_va, WH_va, st_va = _val_arrays(va)
    out = Path(out_root) / name
    out.mkdir(parents=True, exist_ok=True)
    cfg = {"in_ch": in_ch, "vector_head": vector_head, "mode": mode, "lam": lam,
           "features": list(splits.get("features") or []),
           "augment": bool(augment), "hyper": dict(hyper)}
    mu0 = mu0_from_stats(splits["mu"], splits["sigma"]).to(device) if augment \
        else None
    mu0_p = mu0_from_stats(splits["mu_phase"], splits["sigma_phase"]).to(device) \
        if augment and splits.get("mu_phase") is not None else None
    best = {"epoch": -1, "pck02": -1.0}
    amp = torch.autocast("cuda", dtype=torch.bfloat16) if device.startswith("cuda") \
        else torch.autocast("cpu", enabled=False)
    with open(out / "log.jsonl", "w", encoding="utf-8") as logf:
        for ep in range(epochs):
            model.train()
            perm = torch.randperm(len(tr.X), device=device)
            tot = {"coord": 0.0, "conf": 0.0}              # detached tensor accumulation (step sync blocked)
            for s in range(steps):
                b = perm[s * B:(s + 1) * B]
                xb = Xtr[b].float()
                if augment:
                    xb = augment_batch(xb, mu0, mu0_phase=mu0_p)
                with amp:
                    pred = model(xb)
                    loss, parts = pose_loss(pred, Ytr[b], Ptr[b], mode=mode, lam=lam)
                opt.zero_grad(set_to_none=True)
                loss.backward()
                opt.step()
                sched.step()
                tot = {k: tot[k] + parts[k] for k in tot}
            pred_xy, _ = predict(model, va.X, device=device)
            rep = evaluate(pred_xy[mva], xy_va, c_va, WH_va, kappa=kappa, stype=st_va)
            pck02 = rep["pck"]["0.2"] or 0.0
            row = {"epoch": ep, "coord": float(tot["coord"]) / steps,
                   "conf": float(tot["conf"]) / steps,
                   "val_pck02": pck02, "val_pck05": rep["pck"]["0.5"],
                   "lr": opt.param_groups[0]["lr"]}
            logf.write(json.dumps(row) + "\n")
            logf.flush()
            progress(f"ep{ep:02d} coord={row['coord']:.5f} conf={row['conf']:.5f} "
                     f"PCK@0.2={pck02:.3f}")
            if pck02 > best["pck02"]:
                best = {"epoch": ep, "pck02": pck02}
                save_ckpt(out / "best.pt", model, mu=splits["mu"], sigma=splits["sigma"],
                          config=cfg, kappa=kappa, best=best,
                          mu_phase=splits.get("mu_phase"),
                          sigma_phase=splits.get("sigma_phase"))
    return {"best_pck02": best["pck02"], "ckpt": str(out / "best.pt"), "epochs": epochs}
