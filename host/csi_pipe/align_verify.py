"""Alignment verification core.

No cv2/serial/MQTT dependency -- TDD target.
4 functions + verdict + GapEvent dataclass.
"""
from dataclasses import dataclass

import numpy as np


@dataclass
class GapEvent:
    """One clustered gap -- median t_ns (float64 ns)."""
    t_ns: float


# -- detect_gaps --

def detect_gaps(t_fit_by_rx: dict, *, min_gap_ms: float = 60.0) -> list:
    """Clock-fit corrected time arrays per rx {rx: np.ndarray} for tx0 link -> GapEvent list.

    Gap = consecutive sample interval > min_gap_ms. Gap start = previous sample t_fit.
    Detected per RX, then clustered by time (+-200ms) -> 1 median per cluster.
    """
    min_gap_ns = min_gap_ms * 1_000_000

    # Collect gap start times from each RX
    all_starts = []
    for t_arr in t_fit_by_rx.values():
        t = np.asarray(t_arr, dtype=np.float64)
        if len(t) < 2:
            continue
        diffs = np.diff(t)
        gap_idx = np.flatnonzero(diffs > min_gap_ns)
        for i in gap_idx:
            all_starts.append(t[i])  # Previous sample is gap start

    if not all_starts:
        return []

    # Cluster by time (+-200ms) -- merge new element if within 200ms of cluster start
    # 200ms exactly = separate cluster boundary (< 200ms merges)
    cluster_win_ns = 199_999_999.0  # <200ms tolerance (200ms is separate cluster)
    all_starts_sorted = sorted(all_starts)
    clusters = []
    current = [all_starts_sorted[0]]
    for s in all_starts_sorted[1:]:
        if s - current[0] <= cluster_win_ns:
            current.append(s)
        else:
            clusters.append(current)
            current = [s]
    clusters.append(current)

    # Median per cluster
    return [GapEvent(t_ns=float(np.median(c))) for c in clusters]


# -- csi_absolute_offsets --

def csi_absolute_offsets(gap_starts: list, cmd_times) -> dict:
    """Pairing (nearest, discard > +-500ms) -> offset statistics.

    gap_starts: list[GapEvent]
    cmd_times: array-like of float (ns)
    Returns: {n, mean_ms, se_ms, p5, p95, matched, unmatched}
    """
    max_pair_ns = 500_000_000.0  # 500ms
    cmd = np.asarray(cmd_times, dtype=np.float64)

    if len(gap_starts) == 0 or len(cmd) == 0:
        return {"n": 0, "mean_ms": float("nan"), "se_ms": float("nan"),
                "p5": float("nan"), "p95": float("nan"),
                "matched": 0, "unmatched": int(len(cmd))}

    gap_t = np.asarray([g.t_ns for g in gap_starts], dtype=np.float64)

    # Greedy first-come pairing (each gap takes the nearest cmd, gaps go first)
    offsets = []
    used_cmd = set()
    for gt in gap_t:
        dists = np.abs(cmd - gt)
        best = int(np.argmin(dists))
        if dists[best] <= max_pair_ns and best not in used_cmd:
            offsets.append((gt - cmd[best]) / 1_000_000)  # ms
            used_cmd.add(best)

    n = len(offsets)
    unmatched = len(cmd) - len(used_cmd)

    if n == 0:
        return {"n": 0, "mean_ms": float("nan"), "se_ms": float("nan"),
                "p5": float("nan"), "p95": float("nan"),
                "matched": 0, "unmatched": int(unmatched)}

    arr = np.asarray(offsets)
    se = float(arr.std(ddof=1) / np.sqrt(n)) if n > 1 else float("nan")
    return {
        "n": n,
        "mean_ms": float(arr.mean()),
        "se_ms": se,
        "p5": float(np.percentile(arr, 5)),
        "p95": float(np.percentile(arr, 95)),
        "matched": n,
        "unmatched": int(unmatched),
    }


# -- flip_offsets --

def flip_offsets(flip_times, frame_t_ns, frame_brightness) -> dict:
    """Detect flip edges from frame brightness time series (difference threshold) -> edge frame t_ns - flip_time.

    flip_times: array-like int64 ns
    frame_t_ns: array-like int64 ns
    frame_brightness: array-like float
    Returns: {n, mean_ms, se_ms, p5, p95, matched, unmatched}
    """
    flip = np.asarray(flip_times, dtype=np.int64)
    ft = np.asarray(frame_t_ns, dtype=np.int64)
    fb = np.asarray(frame_brightness, dtype=np.float64)

    _empty = {"n": 0, "mean_ms": float("nan"), "se_ms": float("nan"),
              "p5": float("nan"), "p95": float("nan"), "matched": 0, "unmatched": 0}

    if len(ft) < 2 or len(fb) < 2 or len(flip) == 0:
        return _empty

    # Edge detection via difference -- threshold: 30% of brightness range
    diffs = np.diff(fb.astype(np.float64))
    thresh = max(30.0, 0.3 * (fb.max() - fb.min()))
    edge_idx = np.flatnonzero(np.abs(diffs) >= thresh)
    if len(edge_idx) == 0:
        return _empty

    edge_t = ft[edge_idx + 1]  # First frame after edge t_ns

    # Pair each flip_time with nearest edge (+-2 seconds)
    max_pair_ns = 2_000_000_000
    offsets = []
    used_edge = set()
    for ft_flip in flip:
        dists = np.abs(edge_t - ft_flip)
        best = int(np.argmin(dists))
        if dists[best] <= max_pair_ns and best not in used_edge:
            offsets.append((edge_t[best] - ft_flip) / 1_000_000)  # ms
            used_edge.add(best)

    n = len(offsets)
    unmatched = len(flip) - n

    if n == 0:
        return {"n": 0, "mean_ms": float("nan"), "se_ms": float("nan"),
                "p5": float("nan"), "p95": float("nan"), "matched": 0,
                "unmatched": int(unmatched)}

    arr = np.asarray(offsets)
    se = float(arr.std(ddof=1) / np.sqrt(n)) if n > 1 else float("nan")
    return {
        "n": n,
        "mean_ms": float(arr.mean()),
        "se_ms": se,
        "p5": float(np.percentile(arr, 5)),
        "p95": float(np.percentile(arr, 95)),
        "matched": n,
        "unmatched": int(unmatched),
    }


# -- match_frames_by_idx --

def match_frames_by_idx(brightness, video_frame_idx, video_t_ns):
    """Align mp4 frame brightness with session cam/meta stamps by frame_idx -> (t_ns, brightness).

    cam_capture always calls handle_frame(publish) and writer.write(mp4) as a pair
    -> mp4 sequence k = frame_idx k. Session HDF5 may be a subset due to recorder/MQTT loss,
    so only the intersection is returned. frame_idx outside mp4 length (recorder ran longer) discarded.
    HDF5 u64/u32 input -> int64/float64 normalization (safe for subsequent difference operations).
    """
    b = np.asarray(brightness, dtype=np.float64)
    idx = np.asarray(video_frame_idx, dtype=np.int64)
    t = np.asarray(video_t_ns).astype(np.int64)
    m = (idx >= 0) & (idx < len(b))
    idx, t = idx[m], t[m]
    order = np.argsort(idx, kind="stable")     # Guarantees time order (prerequisite for brightness diff)
    idx, t = idx[order], t[order]
    return t, b[idx]


# -- camera_correction_ms --

def camera_correction_ms(mean_ms, frame_t_ns, *, display_latency_ms=13.0):
    """Camera system correction = mean - display latency - T_frame/2 (spec formula).

    T_frame is the median of measured video_t_ns intervals, not the nominal fps --
    the median absorbs any 2x interval created by recorder loss.
    Returns NaN if <2 frames (T_frame cannot be computed).
    """
    t = np.asarray(frame_t_ns).astype(np.int64)
    if len(t) < 2:
        return float("nan")
    t_frame_ms = float(np.median(np.diff(t))) / 1e6
    return float(mean_ms) - float(display_latency_ms) - t_frame_ms / 2.0


# -- jitter_stats --

def jitter_stats(cam_t_ns, clockfit_residuals_ms) -> dict:
    """Jitter statistics from cam intervals and clock-fit residuals.

    cam interval: diff -> |interval - median| p95 (robust, not sigma)
    Returns: {cam_interval_p95_ms, cam_sigma_ms, clockfit_resid_p95_ms}
    """
    cam_t = np.asarray(cam_t_ns, dtype=np.float64)
    resid = np.asarray(clockfit_residuals_ms, dtype=np.float64)

    cam_interval_p95 = 0.0
    cam_sigma = 0.0
    if len(cam_t) >= 2:
        intervals_ms = np.diff(cam_t) / 1_000_000
        med = float(np.median(intervals_ms))
        dev = np.abs(intervals_ms - med)
        cam_interval_p95 = float(np.percentile(dev, 95))
        cam_sigma = float(intervals_ms.std(ddof=1)) if len(intervals_ms) > 1 else 0.0

    if len(resid) == 0:
        clockfit_p95 = 0.0
    else:
        clockfit_p95 = float(np.percentile(np.abs(resid), 95))

    return {
        "cam_interval_p95_ms": cam_interval_p95,
        "cam_sigma_ms": cam_sigma,
        "clockfit_resid_p95_ms": clockfit_p95,
    }


# -- verdict --

def verdict(csi_abs: dict, jitter: dict, *,
            abs_gate_ms: float = 10.0,
            se_gate_ms: float = 2.0,
            jitter_gate_ms: float = 10.0,
            flip_result: dict = None) -> dict:
    """Section 13 v1.5.1 verdict.

    csi_ok  = se < se_gate_ms AND |mean| < abs_gate_ms -- no model anchor dependency
              (v1.5.1: old +5ms anchor discarded due to gap-start semantic mismatch -- see spec.
               mean is always included as csi_correction_ms, treated same as camera ②)
    jitter_ok = cam sigma < jitter_gate_ms AND csi_jitter < jitter_gate_ms
              (v1.5.1: csi_jitter = sqrt(max(0, n*se^2 - T^2/12)) -- t_fit jitter after
               subtracting beacon phase quotient. T = csi_abs["period_ms"], NaN if absent
               -> FAIL(fail-loud). clockfit resid p95 is bridge chunk delivery distribution,
               excluded from gate -- recorded for reference only)
    Camera offset is included as correction_ms without gate (when flip_result is present).
    """
    mean_ms = csi_abs.get("mean_ms", float("nan"))
    se_ms = csi_abs.get("se_ms", float("nan"))
    csi_ok = bool(se_ms < se_gate_ms) and bool(abs(mean_ms) < abs_gate_ms)

    n = csi_abs.get("n", 0)
    period_ms = csi_abs.get("period_ms", float("nan"))
    var_excess = n * se_ms ** 2 - period_ms ** 2 / 12.0  # sigma_shot^2 - phase quotient
    if var_excess != var_excess:  # NaN (period_ms/se missing) -- fail-loud
        csi_jitter_ms = float("nan")
    else:
        csi_jitter_ms = max(0.0, var_excess) ** 0.5

    cam_sigma = jitter.get("cam_sigma_ms", 0.0)
    jitter_ok = bool(cam_sigma < jitter_gate_ms) and bool(csi_jitter_ms < jitter_gate_ms)

    out = {
        "csi_ok": csi_ok,
        "jitter_ok": jitter_ok,
        "pass": csi_ok and jitter_ok,
        "csi_correction_ms": float(mean_ms),
        "csi_jitter_ms": float(csi_jitter_ms),
    }

    if flip_result is not None:
        # Corrected value (correction_ms = mean - 13ms - T_frame/2, computed by CLI) takes priority,
        # old result JSON (pre-correction) falls back to raw mean -- recorded without gate
        out["correction_ms"] = flip_result.get(
            "correction_ms", flip_result.get("mean_ms", float("nan")))

    return out
