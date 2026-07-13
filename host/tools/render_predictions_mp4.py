#!/usr/bin/env python3
"""render_predictions_mp4.py — write an MP4 with predicted 18-joint skeletons overlaid on cam frames.

Bypasses rt/demo.py feature gate (it doesn't know how to stream phase/rssi) and runs
inference directly on the saved /samples/* from the val split. Use the best ckpt you have.

Usage:
  python3 render_predictions_mp4.py \
      --h5 data/s01-rX-20260712-164531.h5 \
      --ckpt runs/s01-rX-norm/best.pt \
      --mp4 data/s01-rX-20260712-164551.mp4 \
      --out runs/s01-rX-norm/inspect.mp4 \
      --fps 21
"""
import argparse
import sys
from pathlib import Path

import cv2
import h5py
import numpy as np
import torch

PROJ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJ / "train"))
from csi_train.data import l2_normalize, rssi_rescale, apply_stats  # noqa: E402
from csi_train.fit import load_ckpt  # noqa: E402

EDGES = [(0, 1), (1, 2), (2, 3), (0, 4), (4, 5), (5, 6),
         (0, 7), (7, 8), (8, 9), (9, 10), (8, 11), (11, 12),
         (12, 13), (8, 14), (14, 15), (15, 16)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h5", required=True, help="Source h5 (with /video/t_ns, /video/frame_idx)")
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--mp4", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=float, default=21.0)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--max-frames", type=int, default=0, help="0 = all")
    a = ap.parse_args()

    device = a.device if (a.device == "cpu" or torch.cuda.is_available()) else "cuda"
    model, ck = load_ckpt(a.ckpt, device=device)
    feats = tuple(ck["config"].get("features") or ())
    mu = ck["mu"].cpu().numpy().astype(np.float32)
    sigma = ck["sigma"].cpu().numpy().astype(np.float32)
    mp = ck.get("mu_phase"); sp = ck.get("sigma_phase")
    mu_p = mp.cpu().numpy().astype(np.float32) if mp is not None else None
    sp_p = sp.cpu().numpy().astype(np.float32) if sp is not None else None

    with h5py.File(a.h5, "r") as h:
        # /samples built by build_samples: indices aligned to /video/t_ns sorted
        X = h["samples/X"][...].astype(np.float32)
        XP = h["samples/X_phase"][...].astype(np.float32) if "samples/X_phase" in h else None
        RS = h["samples/rssi"][...].astype(np.float32) if "samples/rssi" in h else None
        valid = h["samples/valid"][...].astype(bool)
        anchor_t = h["samples/t_ns"][...].astype(np.int64)
        W = int(h["labels"].attrs["W"]); H = int(h["labels"].attrs["H"])
        vid_t = h["video/t_ns"][...].astype(np.int64)
        vid_fi = h["video/frame_idx"][...].astype(np.int64)
        labels_pose18 = h["labels/pose18"][...]  # (F_mp4, 18, 3)
        # Build anchor_t -> nearest mp4 frame_idx
        # anchors correspond to /video/t_ns sorted; vid_fi is parallel
        # anchor i -> vid_t[want] where want is index of anchor_t[i] in vid_t
        # Both should be sorted, so we can searchsorted
        # /samples were built from video/t_ns (anchor source) — we rely on the build ordering
        # We just do searchsorted
        anchor_to_mp4 = np.searchsorted(vid_t, anchor_t).clip(0, len(vid_t) - 1)
        # Also handle off-by-one: if anchor_t is exact match, that's the right frame
        # If anchor_t[i] lies between two vid_t, searchsorted gives the right-side index.
        # We want the closest:
        for k in range(len(anchor_t)):
            idx = anchor_to_mp4[k]
            if idx > 0 and abs(vid_t[idx - 1] - anchor_t[k]) < abs(vid_t[idx] - anchor_t[k]):
                anchor_to_mp4[k] = idx - 1

    # Normalize X
    print(f"Feats: {feats}; X shape: {X.shape}; valid: {valid.sum()}/{len(valid)}", flush=True)
    Xn = l2_normalize(X)
    if "rssi" in feats and RS is not None:
        Xn = rssi_rescale(Xn, RS)
    Xn = apply_stats(Xn, mu, sigma)
    if "phase" in feats and XP is not None and mu_p is not None:
        Xn = np.concatenate([Xn, apply_stats(XP, mu_p, sp_p)], axis=1)
    Xn = Xn.astype(np.float32)

    # Predict on all valid rows
    pred_xy = np.zeros((len(Xn), 18, 2), np.float32)
    pred_c = np.zeros((len(Xn), 18), np.float32)
    bs = 8 if device == "cpu" else 32
    with torch.no_grad():
        for s in range(0, len(Xn), bs):
            xb = torch.from_numpy(np.ascontiguousarray(Xn[s:s + bs])).to(device)
            op = model(xb)
            if op.dim() == 4:
                d_idx = torch.arange(op.shape[-1], device=op.device)
                xy = torch.stack([op[:, 0, d_idx, d_idx], op[:, 1, d_idx, d_idx]], dim=2)
                c = op[:, 2, d_idx, d_idx]
            else:
                xy = op[:, :2].transpose(1, 2)
                c = op[:, 2]
            pred_xy[s:s + bs] = xy.cpu().numpy()
            pred_c[s:s + bs] = c.cpu().numpy()
    print(f"Predicted {len(Xn)} rows on {device}", flush=True)

    # Open mp4 + write output
    cap = cv2.VideoCapture(str(a.mp4))
    if not cap.isOpened():
        raise SystemExit(f"mp4 open failed: {a.mp4}")
    F = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if a.max_frames:
        F = min(F, a.max_frames)
    fps_in = cap.get(cv2.CAP_PROP_FPS)
    W0 = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H0 = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"mp4: {F} frames @ {fps_in:.1f}fps, {W0}x{H0}; out: {a.out} @ {a.fps}fps", flush=True)

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(a.out), fourcc, a.fps, (W0, H0))
    if not writer.isOpened():
        raise SystemExit(f"writer open failed: {a.out}")

    # Build anchor array indexed by mp4 frame_idx for fast lookup
    # Each mp4 frame f -> nearest anchor index (use anchor_to_mp4 mapping)
    # For each mp4 frame, find anchors whose mp4 mapping == f
    # Simpler: for each mp4 frame index f, find anchor i such that vid_fi[anchor_to_mp4[i]] == f
    # Actually we have anchor_t and vid_t both sorted; anchor_to_mp4[i] is index into vid_t
    # corresponding anchor's position. Then mp4 frame = vid_fi[anchor_to_mp4[i]].
    # So: for each mp4 frame f, find any anchor i where vid_fi[anchor_to_mp4[i]] == f
    # Group by mp4 frame:
    mp4_of_anchor = vid_fi[anchor_to_mp4]   # (N_anchors,)
    anchors_by_mp4 = {}
    for i, f in enumerate(mp4_of_anchor):
        if not valid[i]:
            continue
        anchors_by_mp4.setdefault(int(f), []).append(i)
    print(f"Grouped anchors: {len(anchors_by_mp4)} mp4 frames have anchors", flush=True)

    for f in range(F):
        ok, frame = cap.read()
        if not ok:
            break
        if f in anchors_by_mp4:
            # Average predictions for this mp4 frame (typically 1 anchor per frame)
            for i in anchors_by_mp4[f]:
                px = pred_xy[i, :, 0] * W
                py = pred_xy[i, :, 1] * H
                # GT from /labels/pose18[f]
                gt = labels_pose18[f] if f < len(labels_pose18) else None
                if gt is not None:
                    for j0, j1 in EDGES:
                        if gt[j0, 2] > 0.3 and gt[j1, 2] > 0.3:
                            cv2.line(frame, (int(gt[j0, 0]), int(gt[j0, 1])),
                                     (int(gt[j1, 0]), int(gt[j1, 1])), (255, 0, 0), 2)
                    for j in range(18):
                        if gt[j, 2] > 0.3:
                            cv2.circle(frame, (int(gt[j, 0]), int(gt[j, 1])), 3, (255, 0, 0), -1)
                # Pred (red)
                for j0, j1 in EDGES:
                    cv2.line(frame, (int(px[j0]), int(py[j0])),
                             (int(px[j1]), int(py[j1])), (0, 0, 255), 1)
                for j in range(18):
                    cv2.circle(frame, (int(px[j]), int(py[j])), 2, (0, 0, 255), -1)
                # Title
                cv2.putText(frame, f"frame {f} | anchor {i} | conf {float(pred_c[i].mean()):.2f}",
                            (5, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
                break  # one anchor per frame is typical
        writer.write(frame)
        if f % 200 == 0:
            print(f"  frame {f}/{F}", flush=True)

    cap.release()
    writer.release()
    print(f"Saved: {a.out}  ({F} frames)", flush=True)


if __name__ == "__main__":
    main()