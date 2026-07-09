# Non-learning baseline — gate threshold = max of 1)2)3) PCK@0.2.
# All in CSI input/normalized coordinate space. Input X·GT are presence=1 rows only (caller filters).
# 2' oracle_centroid sees GT — diagnostic only, excluded from gate.
import numpy as np
import torch

RHIP, LHIP = 8, 11


def hip_center(gt_xy):
    """(...,18,2) → (...,2) — (RHip+LHip)/2."""
    return (gt_xy[..., RHIP, :] + gt_xy[..., LHIP, :]) / 2


def mean_pose(gt_xy):
    if len(gt_xy) == 0:
        raise ValueError("mean_pose: empty GT -- no presence=1 rows")
    return gt_xy.mean(axis=0)


def predict_mean(mp, n):
    return np.broadcast_to(mp, (n,) + mp.shape).copy()


def knn_indices(train_X, query_X, k, *, device="cpu", chunk=4096):
    """f32 flat L2 distance k-NN -- (Q,k) indices. GPU chunk processing (f16 input upcast to f32 -- 2520-dim cumulative precision)."""
    if len(train_X) == 0 or len(query_X) == 0:
        raise ValueError(f"k-NN empty input: train {len(train_X)} rows, query {len(query_X)} rows")
    if k > len(train_X):
        raise ValueError(f"k={k} > train {len(train_X)} rows")
    tr = torch.from_numpy(np.ascontiguousarray(train_X, dtype=np.float32)
                          .reshape(len(train_X), -1)).to(device)
    q = torch.from_numpy(np.ascontiguousarray(query_X, dtype=np.float32)
                         .reshape(len(query_X), -1))
    out = np.empty((len(query_X), k), np.int64)
    with torch.no_grad():
        for s in range(0, len(q), chunk):
            d = torch.cdist(q[s:s + chunk].to(device), tr)
            out[s:s + chunk] = d.topk(k, largest=False).indices.cpu().numpy()
    return out


def predict_knn_pose(train_X, train_gt_xy, query_X, *, device="cpu"):
    """③ Copy GT pose from k=1 nearest train row.

    Assumes train≠query -- same-set evaluation uses self-copy which inflates PCK (val only).
    """
    idx = knn_indices(train_X, query_X, 1, device=device)[:, 0]
    return train_gt_xy[idx].copy()


def predict_knn_centroid(train_X, train_gt_xy, query_X, *, k=5, device="cpu"):
    # 2) Estimate centroid via k-NN hip center mean -> average pose translation.
    idx = knn_indices(train_X, query_X, k, device=device)
    cent = hip_center(train_gt_xy[idx]).mean(axis=1)           # (Q,2)
    mp = mean_pose(train_gt_xy)
    return mp[None] + (cent - hip_center(mp[None]))[:, None, :]


def predict_oracle_centroid(train_gt_xy, query_gt_xy):
    # 2' GT centroid oracle -- diagnostic only.
    mp = mean_pose(train_gt_xy)
    return mp[None] + (hip_center(query_gt_xy) - hip_center(mp[None]))[:, None, :]
