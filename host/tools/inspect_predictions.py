#!/usr/bin/env python3
"""inspect_predictions.py — visualize WiSPPN predictions vs GT on val split.

Loads best.pt, runs on a few rows of /samples/X, plots predicted (red) vs GT (blue)
18-joint skeletons on the cam frame (mp4) for visual sanity check.

Usage:
  python3 inspect_predictions.py --h5 data/processed/s01-rX/s01-rX-20260712-164531-val.h5 \
      --ckpt runs/s01-rX-norm/best.pt --mp4 data/s01-rX-20260712-164551.mp4 \
      --out runs/s01-rX-norm/inspect --n 8
"""
import argparse
import sys
from pathlib import Path

import cv2
import h5py
import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from csi_pipe.samples import resolve_corrections  # noqa: E402

PROJ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJ / "train"))
sys.path.insert(0, str(PROJ / "teacher"))
from csi_train.data import l2_normalize, rssi_rescale, apply_stats  # noqa: E402
from csi_train.fit import load_ckpt  # noqa: E402

# COCO-18 skeleton edges (subset of standard)
EDGES = [(0, 1), (1, 2), (2, 3), (0, 4), (4, 5), (5, 6),
         (0, 7), (7, 8), (8, 9), (9, 10), (8, 11), (11, 12),
         (12, 13), (8, 14), (14, 15), (15, 16)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h5", required=True)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--mp4", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--device", default="cuda")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)

    device = a.device if torch.cuda.is_available() else "cpu"
    model, ck = load_ckpt(a.ckpt, device=device)
    feats = tuple(ck["config"].get("features") or ())
    mu = ck["mu"].cpu().numpy().astype(np.float32)
    sigma = ck["sigma"].cpu().numpy().astype(np.float32)
    mp = ck.get("mu_phase")
    sp = ck.get("sigma_phase")
    mu_p = mp.cpu().numpy().astype(np.float32) if mp is not None else None
    sp_p = sp.cpu().numpy().astype(np.float32) if sp is not None else None

    with h5py.File(a.h5, "r") as h:
        X = h["samples/X"][...].astype(np.float32)
        XP = h["samples/X_phase"][...].astype(np.float32) if "samples/X_phase" in h else None
        RS = h["samples/rssi"][...].astype(np.float32) if "samples/rssi" in h else None
        valid = h["samples/valid"][...].astype(bool)
        t_ns = h["samples/t_ns"][...]
        W = int(h["labels"].attrs["W"])
        H = int(h["labels"].attrs["H"])
        # Use GT PAM diagonal to get (x_px, y_px) for ground-truth 18 joints
        Y = h["samples/Y"][...].astype(np.float32)   # (N, 4, 18, 18)

    mask = valid
    idx_all = np.where(mask)[0]
    if len(idx_all) < a.n:
        a.n = len(idx_all)
    np.random.seed(0)
    pick = np.sort(np.random.choice(idx_all, a.n, replace=False))
    print(f"Valid rows: {mask.sum()}/{len(mask)}, picked: {pick.tolist()}")

    # Build normalized X
    Xn = l2_normalize(X)
    if "rssi" in feats and RS is not None:
        Xn = rssi_rescale(Xn, RS)
    Xn = apply_stats(Xn, mu, sigma)
    if "phase" in feats and XP is not None and mu_p is not None:
        Xn = np.concatenate([Xn, apply_stats(XP, mu_p, sp_p)], axis=1)
    Xn = Xn.astype(np.float32)

    # GT: from Y diagonal (x_norm, y_norm, vis)
    d = np.arange(Y.shape[-1])
    gt_x_norm = Y[:, 0, d, d]  # (N, 18)
    gt_y_norm = Y[:, 1, d, d]
    gt_v = Y[:, 2, d, d]

    # Predict
    with torch.no_grad():
        out_pred = []
        for s in range(0, len(Xn), 8):
            xb = torch.from_numpy(np.ascontiguousarray(Xn[s:s + 64])).to(device)
            op = model(xb)
            if op.dim() == 4:
                # PAM head (4, 18, 18) — diagonal
                d_idx = torch.arange(op.shape[-1], device=op.device)
                xy = torch.stack([op[:, 0, d_idx, d_idx], op[:, 1, d_idx, d_idx]], dim=2)
                c = op[:, 2, d_idx, d_idx]
            else:
                xy = op[:, :2].transpose(1, 2)
                c = op[:, 2]
            out_pred.append((xy.cpu().numpy(), c.cpu().numpy()))
    pred_xy = np.concatenate([o[0] for o in out_pred])  # (N, 18, 2)
    pred_c = np.concatenate([o[1] for o in out_pred])  # (N, 18)

    # Open mp4 and seek to nearest cam frame for each anchor t_ns
    cap = cv2.VideoCapture(str(a.mp4))
    if not cap.isOpened():
        raise SystemExit(f"Failed to open mp4: {a.mp4}")
    with h5py.File(a.h5.replace("-val.h5", ".h5").replace("-train.h5", ".h5"), "r") as h_src:
        if "video" not in h_src or "video/t_ns" not in h_src:
            raise SystemExit("source h5 missing /video/t_ns")
        # We'll just use the split h5 — it doesn't have /video. Open original:
    # Re-open original to get /video/t_ns
    orig = a.h5.split("/processed/")[0] + "/" + a.h5.split("/")[-1].split("-val")[0].split("-train")[0] + ".h5"
    if not Path(orig).exists():
        # try several alternatives
        candidates = list(Path(a.h5.split("/processed/")[0]).glob("*.h5"))
        orig = str([c for c in candidates if a.h5.split("/")[-1].split("-")[1:3] == c.stem.split("-")[1:3]][0])
    with h5py.File(orig, "r") as h_src:
        vid_t = h_src["video/t_ns"][...].astype(np.int64)
        vid_fi = h_src["video/frame_idx"][...].astype(np.int64)

    for k, i in enumerate(pick):
        # Find nearest mp4 frame
        want = int(np.searchsorted(vid_t, t_ns[i]))
        want = max(0, min(want - 1, len(vid_t) - 1))
        target = int(vid_fi[want])
        cap.set(cv2.CAP_PROP_POS_FRAMES, target)
        ok, frame = cap.read()
        if not ok:
            print(f"  [{k}] i={i} seek to {target} failed")
            continue

        # Predicted (from normalized coords, scale by W/H)
        pred_x_px = pred_xy[i, :, 0] * W
        pred_y_px = pred_xy[i, :, 1] * H
        # GT (x_norm = x_px/W originally, but Y is *normalized* diff — diagonal ch0=0, ch1=0)
        # Actually Y[0, j, j] is x_r - x_r = 0, Y[1, j, j] = 0, Y[2] = c_r^2
        # So the GT 2D coords are NOT in Y diagonal — we need /labels/pose18 for absolute pixels
        # Fall back to /labels/pose18 (cam-frame indexed, indexed by anchor's t_ns)
        with h5py.File(orig, "r") as h_src:
            lbl_t = h_src["labels/pose18"][...]  # (F, 18, 3)
        # find label row whose t is closest
        # Actually /labels/* is mp4-frame indexed; the build_samples anchors already aligned
        # We don't have the anchor->label mapping here. Skip absolute GT, use:
        # compute "GT" 2D from Y diagonal reversed: but Y is constructed as (x_r-x_c, y_r-y_c, c_r*c_c)
        # so for diagonal ch0=0, ch1=0, ch2=visibility^2. We can recover approximate x via projection:
        # For 0/4/7/8 anchors (head, shoulders, hips), their coords are on skeleton — but we don't
        # have them. So this visualization focuses on confidence map.
        # Better: open the original session h5 which has /labels/pose18 (mp4 frame indexed)
        # and map mp4 frame_idx back to /samples anchor t_ns
        # Build t_ns -> mp4 frame_idx
        # /samples/anchor was /video/t_ns sorted, and /video/frame_idx maps to mp4 index
        # So anchor i corresponds to mp4 frame vid_fi[want] where want is searchsorted for anchor's t_ns
        # pose18 is indexed by mp4 frame_idx 0..F-1, so pose18[vid_fi[want]] is the GT
        gt_pose = lbl_t[vid_fi[want]]   # (18, 3) [x_px, y_px, score]
        gt_ok = gt_pose[:, 2] > 0.3
        gt_x_px = np.where(gt_ok, gt_pose[:, 0], np.nan)
        gt_y_px = np.where(gt_ok, gt_pose[:, 1], np.nan)

        # Draw GT (blue) and pred (red)
        for j0, j1 in EDGES:
            if not (np.isnan(gt_x_px[j0]) or np.isnan(gt_x_px[j1])):
                cv2.line(frame, (int(gt_x_px[j0]), int(gt_y_px[j0])),
                         (int(gt_x_px[j1]), int(gt_y_px[j1])), (255, 0, 0), 2)
        for j0, j1 in EDGES:
            cv2.line(frame, (int(pred_x_px[j0]), int(pred_y_px[j0])),
                     (int(pred_x_px[j1]), int(pred_y_px[j1])), (0, 0, 255), 1)
        for j in range(18):
            if not np.isnan(gt_x_px[j]):
                cv2.circle(frame, (int(gt_x_px[j]), int(gt_y_px[j])), 3, (255, 0, 0), -1)
            cv2.circle(frame, (int(pred_x_px[j]), int(pred_y_px[j])), 2, (0, 0, 255), -1)

        # Stats for this row
        # Confidence
        mpjpe = np.nanmean(np.sqrt((pred_x_px - gt_x_px) ** 2 + (pred_y_px - gt_y_px) ** 2))
        label = f"i={i} mpjpe={mpjpe:.1f}px conf_mean={float(pred_c[i].mean()):.2f}"
        cv2.putText(frame, label, (5, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        out_p = out / f"pred_{k:02d}_i{i}_f{target}.png"
        cv2.imwrite(str(out_p), frame)
        print(f"  [{k}] i={i} t_ns={t_ns[i]} target_frame={target} -> {out_p}  ({label})")

    cap.release()
    print(f"Saved {a.n} visualizations to {out}/")


if __name__ == "__main__":
    main()