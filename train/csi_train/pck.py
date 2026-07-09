# PCK@alpha — pixel space, excludes GT c<0.3 joints, lying aspect-ratio-corrected denominator.
# Evaluation input is presence=1 rows only (caller filters). Coordinates are normalized — internal W·H pixel conversion.
# Lying judgment = core (shoulders2·pelvis2·knees2·nose) bbox h/w < 0.8 (valid core >=3),
# denominator = max(torso diagonal, kappa·core bbox diagonal). kappa calibrated from train upright (h/w>=1.2).
import numpy as np

NOSE, RSHO, LSHO, RHIP, LHIP, RKNEE, LKNEE = 0, 2, 5, 8, 11, 9, 12
CORE = (RSHO, LSHO, RHIP, LHIP, RKNEE, LKNEE, NOSE)
C_MIN = 0.3
LYING_AR, UPRIGHT_AR = 0.8, 1.2


def frame_geometry(gt_px, c):
    # Single frame -> (torso, core_diag, aspect, n_core). Invalid = nan.
    torso = np.nan
    if c[LSHO] >= C_MIN and c[RHIP] >= C_MIN:
        torso = float(np.linalg.norm(gt_px[LSHO] - gt_px[RHIP]))
    pts = gt_px[[j for j in CORE if c[j] >= C_MIN]]
    if len(pts) < 3:
        return torso, np.nan, np.nan, len(pts)
    w = float(pts[:, 0].max() - pts[:, 0].min())
    h = float(pts[:, 1].max() - pts[:, 1].min())
    aspect = h / w if w > 0 else np.inf
    return torso, float(np.hypot(w, h)), aspect, len(pts)


def calibrate_kappa(gt_xy, gt_c, WH):
    # Median(torso/core diagonal) from upright frames — computed from train GT.
    px = gt_xy * WH[:, None, :]
    ratios = []
    for f in range(len(px)):
        torso, core, ar, n = frame_geometry(px[f], gt_c[f])
        if n >= 3 and np.isfinite(torso) and np.isfinite(ar) and ar >= UPRIGHT_AR and core > 0:
            ratios.append(torso / core)
    if not ratios:
        # No upright frames — cannot calibrate kappa
        raise ValueError("no upright frames — kappa calibration failed")
    return float(np.median(ratios))


def evaluate(pred_xy, gt_xy, gt_c, WH, *, kappa, alphas=(0.2, 0.5), stype=None,
             lying_override=None):
    # Normalized coordinates (F,18,2)·c (F,18)·WH (F,2) -> report dict (JSON serializable).
    # Aggregation is (frame,joint) pool mean — frames with more valid joints get weighted (not per-frame macro).
    # mpjpe_norm is 16:9 anisotropic space mean — auxiliary metric (px is authoritative). per_joint uses fixed gate alpha=0.2.
    F = len(gt_xy)
    if stype is not None and len(stype) != F:
        raise ValueError(f"stype length {len(stype)} != F {F}")
    if lying_override is not None and len(lying_override) != F:
        raise ValueError(f"lying_override length {len(lying_override)} != F {F}")
    pred_px = pred_xy * WH[:, None, :]
    gt_px = gt_xy * WH[:, None, :]
    dist = np.linalg.norm(pred_px - gt_px, axis=2)            # (F,18)
    valid_j = gt_c >= C_MIN
    den = np.full(F, np.nan)
    lying = np.zeros(F, bool)
    for f in range(F):
        torso, core, ar, n = frame_geometry(gt_px[f], gt_c[f])
        lying[f] = bool(n >= 3 and np.isfinite(ar) and ar < LYING_AR)
        if lying_override is not None:
            lying[f] = bool(lying_override[f])
        if lying[f] and np.isfinite(core):
            den[f] = np.nanmax([torso, kappa * core])
        else:
            den[f] = torso
    ok_f = np.isfinite(den) & (den > 0) & valid_j.any(axis=1)
    use = valid_j & ok_f[:, None]                              # (F,18) evaluation target joints

    def _pck(rows):
        m = use & rows[:, None]
        out = {}
        for a in alphas:
            hits = dist <= a * den[:, None]
            out[str(a)] = float(hits[m].mean()) if m.any() else None
        return out

    rep = {"pck": _pck(np.ones(F, bool)),
           "pck_lying": _pck(lying),
           "per_joint_0.2": [(float((dist[:, j][use[:, j]] <=
                                     0.2 * den[use[:, j]]).mean()) if use[:, j].any() else None)
                             for j in range(gt_xy.shape[1])],
           "mpjpe_px": float(dist[use].mean()) if use.any() else None,
           "mpjpe_norm": float(np.linalg.norm(pred_xy - gt_xy, axis=2)[use].mean())
                         if use.any() else None,
           "kappa": float(kappa),
           "n_eval": int(ok_f.sum()), "n_lying": int((lying & ok_f).sum()),
           "n_excluded": int(F - ok_f.sum())}
    if stype is not None:
        rep["per_type"] = {str(t): _pck(np.asarray(stype) == t)
                           for t in sorted(set(map(str, stype)))}
    return rep
