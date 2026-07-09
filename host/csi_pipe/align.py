"""Alignment stages 1 & 2 -- epoch separation, <=2 consecutive gap interpolation (+mask), 100Hz grid, window trimming.

Order is fixed: resample -> normalize. This module only does resampling -- normalization is in training.
All grid/sample times are based on clock-fit corrected time (t_fit).
"""
from dataclasses import dataclass, field

import numpy as np


def amplitude(iq):
    """iq [n,56,2] int8 -> amplitude [n,56] float32."""
    f = iq.astype(np.float32)
    return np.sqrt(f[..., 0] ** 2 + f[..., 1] ** 2)


_PHASE_K = np.arange(56, dtype=np.float64)
_PHASE_A = np.stack([_PHASE_K, np.ones(56)], axis=1)          # [56,2] = (slope, offset)
_PHASE_P = np.eye(56) - _PHASE_A @ np.linalg.inv(_PHASE_A.T @ _PHASE_A) @ _PHASE_A.T


def sanitized_phase(iq):
    """iq [n,56,2] -> subcarrier axis unwrap + LS linear (STO/CFO) removal residual [n,56] f32 (Section 6.3).

    Even if I/Q column order is reversed, residuals differ only by consistent transformation
    (reflection + constant) -- neutral for learning."""
    f = iq.astype(np.float64)
    phi = np.arctan2(f[..., 1], f[..., 0])
    return (np.unwrap(phi, axis=1) @ _PHASE_P).astype(np.float32)


def split_epochs(seq, boot_id):
    """Split by seq rollback (TX restart) or boot_id change (RX reboot) boundaries."""
    seq = np.asarray(seq, np.int64)
    boot = np.asarray(boot_id, np.int64)
    cut = np.flatnonzero((np.diff(seq) <= 0) | (np.diff(boot) != 0)) + 1
    bounds = np.concatenate(([0], cut, [len(seq)]))
    return [slice(int(a), int(b)) for a, b in zip(bounds[:-1], bounds[1:]) if b > a]


def fill_gaps(t_ns, seq, amp, *, max_run=2):
    """Interpolate seq losses <=max_run consecutive within a single epoch linearly.

    Returns: (t2, amp2, interp_mask, breaks) -- breaks is list of uninterpolated (t_left, t_right)
    intervals (for grid mask, Section 5.2-1 'excess interval discard')."""
    t = np.asarray(t_ns, np.int64)
    seq = np.asarray(seq, np.int64)
    d = np.diff(seq)
    out_t, out_a, out_m, breaks = [], [], [], []
    prev = 0
    for i in np.flatnonzero(d > 1):
        out_t.append(t[prev:i + 1])
        out_a.append(amp[prev:i + 1])
        out_m.append(np.zeros(i + 1 - prev, bool))
        run = int(d[i]) - 1
        if run <= max_run:
            w = (np.arange(1, run + 1, dtype=np.float64) / (run + 1))
            ti = (t[i] + w * (t[i + 1] - t[i])).astype(np.int64)
            ai = amp[i][None, :] * (1 - w[:, None]) + amp[i + 1][None, :] * w[:, None]
            out_t.append(ti)
            out_a.append(ai.astype(amp.dtype))
            out_m.append(np.ones(run, bool))
        else:
            breaks.append((int(t[i]), int(t[i + 1])))
        prev = i + 1
    out_t.append(t[prev:])
    out_a.append(amp[prev:])
    out_m.append(np.zeros(len(t) - prev, bool))
    return (np.concatenate(out_t), np.concatenate(out_a),
            np.concatenate(out_m), breaks)


@dataclass
class LinkStream:
    """Link stream after gap fill and epoch merge (t ascending, breaks sorted)."""
    t: np.ndarray                  # i64 ns (t_fit)
    amp: np.ndarray                # f32 [n,56]
    interp: np.ndarray             # bool [n]
    breaks: list = field(default_factory=list)   # [(t_left, t_right)]


def grid_bounds(streams, *, step_ns=10_000_000):
    """Common available interval across all links -> (g0, g1) -- step-aligned, g0=ceil, g1=floor."""
    lo = max(int(s.t[0]) for s in streams)
    hi = min(int(s.t[-1]) for s in streams)
    g0 = -(-lo // step_ns) * step_ns
    g1 = (hi // step_ns) * step_ns
    return g0, g1


def grid_block(s: LinkStream, tb):
    """Linear interpolate link amplitude at grid time block tb (i64) + mask.

    Mask True: outside stream range / inside break interval / interpolated samples.
    Requires s.t samples n>=2. breaks are disjoint and sorted by start time ascending --
    nested/overlapping breaks are silently dropped (guaranteed by current caller fill_gaps)."""
    tb = np.asarray(tb, np.int64)
    n = len(s.t)
    idx = np.clip(np.searchsorted(s.t, tb), 1, n - 1)
    tl, tr = s.t[idx - 1], s.t[idx]
    w = ((tb - tl) / np.maximum(tr - tl, 1)).astype(np.float32)[:, None]
    amp = s.amp[idx - 1] * (1 - w) + s.amp[idx] * w
    mask = (tb < s.t[0]) | (tb > s.t[-1]) | s.interp[idx - 1] | s.interp[idx]
    if s.breaks:
        starts = np.asarray([b[0] for b in s.breaks], np.int64)
        ends = np.asarray([b[1] for b in s.breaks], np.int64)
        bi = np.searchsorted(starts, tb, side="right") - 1
        in_break = (bi >= 0) & (tb < ends[np.clip(bi, 0, len(ends) - 1)]) & (tb > starts[np.clip(bi, 0, len(starts) - 1)])
        mask |= in_break
    return amp, mask


WIN = 5  # 5 packets x 56SC = 280 channels


def window_indices(g0, step, G, anchors, *, win=WIN):
    """Anchor t_f -> window start grid rows. OK only if [t_f - win*step, t_f) is subset of grid."""
    a = np.asarray(anchors, np.int64)
    off = a - g0
    q, r = np.divmod(off, step)
    ceil = q + (r > 0)
    start = ceil - win
    ok = (start >= 0) & (ceil <= G)
    return start, ok


def cut_windows(amp, mask, starts, *, win=WIN):
    """Grid -> X[N,280,3,3] f16 + valid[N] (link-wise mask >=2/5 -> False)."""
    starts = np.asarray(starts, np.int64)
    idx = starts[:, None] + np.arange(win)[None, :]          # [N,5]
    blk = amp[idx]                                           # [N,5,56,3,3]
    X = blk.reshape(len(starts), win * 56, 3, 3).astype(np.float16)
    bad = mask[idx].sum(axis=1)                              # [N,3,3]
    valid = (bad < 2).all(axis=(1, 2))
    return X, valid
