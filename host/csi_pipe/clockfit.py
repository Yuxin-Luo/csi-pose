"""Per-board esp_timer->t_host clock model.

USB-UART batching jitter only distorts arrival time in the +direction (no early arrival) --
the lower envelope of (esp, t_host) scatter is the true clock line. Oscillator temperature drift
is absorbed by piecewise fit per overlapping window + linear interpolation between window centers
(piecewise continuous).

Numerical note: window internal coordinates are in (seconds, microsecond delay) scale --
ensures float64 precision for fit/extrapolation. Input t_ns absolute (~1.8e18) stays as int64,
model computes offsets from epoch reference point. Fit outputs (t_fit, resid_ns) are float64
absolute ns -- quantization ~256ns, negligible vs 10ms target.
"""
from dataclasses import dataclass, field

import numpy as np

from csi_host.unwrap import TimeUnwrapper

WRAP_US = TimeUnwrapper.WRAP  # esp_timer u32 wrap 2^32 us (71.58 min) -- single source of truth


def lower_hull_idx(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Lower convex hull vertex indices of points sorted by x (monotone chain lower hull).

    Only the minimum-value representative per floor(x) bucket is a candidate (suppresses one-sided jitter noise).
    """
    bucket = np.floor(x).astype(np.int64)
    o = np.lexsort((y, bucket))
    first = np.concatenate(([True], np.diff(bucket[o]) != 0))
    cand = np.sort(o[first])
    hull = []
    for j in range(len(cand)):
        i = cand[j]
        while len(hull) >= 2:
            i1, i2 = cand[hull[-2]], cand[hull[-1]]
            if (x[i2] - x[i1]) * (y[i] - y[i1]) - (y[i2] - y[i1]) * (x[i] - x[i1]) <= 0:
                hull.pop()
            else:
                break
        hull.append(j)
    return cand[np.asarray(hull, dtype=np.int64)]


@dataclass
class EpochModel:
    boot: int
    esp_lo: float          # Fit domain (unwrapped us)
    esp_hi: float
    esp0: float            # Reference point
    t0_ns: int
    centers_s: np.ndarray  # Window centers (relative seconds within epoch)
    coef: np.ndarray       # [k,2] (alpha: us/s = ppm, beta: us)


@dataclass
class FitReport:
    t_fit: np.ndarray      # f64 ns (0 for invalid intervals)
    resid_ns: np.ndarray   # t - t_fit (NaN for invalid)
    valid: np.ndarray      # bool
    slopes: list = field(default_factory=list)  # Per-epoch average slope (us/s = ppm)

    def stats(self) -> dict:
        r = self.resid_ns[self.valid] / 1e6  # ms
        if len(r) == 0:
            return {"n": 0}
        return {
            "n": int(self.valid.sum()),
            "slope_ppm": self.slopes,
            "resid_p5_ms": float(np.percentile(r, 5)),
            "resid_p50_ms": float(np.percentile(r, 50)),
            "resid_p95_ms": float(np.percentile(r, 95)),
            "resid_max_ms": float(r.max()),
        }


def _eval_piecewise(xs, centers, coefs):
    """Linear interpolation of predicted values between window centers (extrapolate with end window model at ends) -- returns us delay."""
    if len(centers) == 1:
        return coefs[0, 0] * xs + coefs[0, 1]
    j = np.clip(np.searchsorted(centers, xs) - 1, 0, len(centers) - 2)
    c0, c1 = centers[j], centers[j + 1]
    w = np.clip((xs - c0) / np.maximum(c1 - c0, 1e-9), 0.0, 1.0)
    p0 = coefs[j, 0] * xs + coefs[j, 1]
    p1 = coefs[j + 1, 0] * xs + coefs[j + 1, 1]
    return (1 - w) * p0 + w * p1


def _fit_epoch(xs, ys, window_s):
    """xs(s), ys(us delay) -> (centers, coefs) or None. Bucket minimum delay -> lower hull -> window LS.

    Bucket width = window_s/20 (minimum 1s) -- compared to maximum USB batching jitter,
    the minimum delay within a bucket must be close enough to 0 for the hull slope to converge.
    1s bucket is insufficient at 30ms jitter.
    """
    bw = max(1.0, window_s / 20.0)
    bucket = (xs // bw).astype(np.int64)
    o = np.lexsort((ys, bucket))
    first = np.concatenate(([True], np.diff(bucket[o]) != 0))
    cand = np.sort(o[first])
    hull = cand[lower_hull_idx(xs[cand], ys[cand])]
    xh, yh = xs[hull], ys[hull]
    span = float(xs[-1])
    starts = (np.arange(0.0, span - window_s + 1e-9, window_s / 2)
              if span > window_s else np.array([0.0]))
    centers, coefs = [], []
    for s in starts:
        m = (xh >= s) & (xh <= s + window_s)
        if m.sum() < 3:
            continue  # Window with too few hull points -- neighbor window models extend coverage (spec error handling)
        a, b = np.polyfit(xh[m], yh[m], 1)
        centers.append((max(s, 0.0) + min(s + window_s, span)) / 2)
        coefs.append((a, b))
    if not centers:
        if len(hull) < 2:
            return None
        a, b = np.polyfit(xh, yh, 1)
        centers, coefs = [span / 2], [(a, b)]
    return np.asarray(centers), np.asarray(coefs)


class BoardClockModel:
    def __init__(self, epochs):
        self.epochs = epochs

    _MARGIN_US = 60e6  # 1 minute domain margin

    def predict(self, esp_us, boot_id):
        """(t_fit_ns f64, valid bool). Epoch match = boot value + esp domain."""
        esp = np.asarray(esp_us, np.float64)
        boot = np.asarray(boot_id)
        t = np.zeros(len(esp), np.float64)
        ok = np.zeros(len(esp), bool)
        for e in self.epochs:
            m = ((boot == e.boot) & (esp >= e.esp_lo - self._MARGIN_US)
                 & (esp <= e.esp_hi + self._MARGIN_US))
            if not m.any():
                continue
            xs = (esp[m] - e.esp0) / 1e6
            d_us = _eval_piecewise(xs, e.centers_s, e.coef)
            t[m] = e.t0_ns + 1000.0 * (esp[m] - e.esp0) + d_us * 1e3
            ok[m] = True
        return t, ok


def fit_board(esp_us, t_ns, boot_id, *, window_s=600.0, min_epoch=100):
    """Board (rx) stream (in arrival order) -> (BoardClockModel, FitReport). Epoch = boot_id change.

    Caution: esp rollback within unchanged boot_id (undetected reboot) is not separated --
    that epoch's lower hull follows the previous section and the large positive residual
    reveals it later (detectable via stats).
    """
    esp = np.asarray(esp_us, np.float64)
    t = np.asarray(t_ns, np.int64)
    boot = np.asarray(boot_id)
    n = len(esp)
    cut = np.flatnonzero(np.diff(boot.astype(np.int64)) != 0) + 1
    bounds = np.concatenate(([0], cut, [n]))
    epochs = []
    t_fit = np.zeros(n, np.float64)
    valid = np.zeros(n, bool)
    slopes = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b - a < min_epoch:
            continue
        order = np.argsort(esp[a:b], kind="stable") + a  # Safety net -- should already be sorted by arrival
        e, ti = esp[order], t[order]
        esp0, t0 = e[0], int(ti[0])
        xs = (e - esp0) / 1e6
        ys = ((ti - t0) - 1000.0 * (e - esp0)) / 1e3     # us delay (int64 diff -> f64 safe)
        fit = _fit_epoch(xs, ys, window_s)
        if fit is None:
            continue
        centers, coefs = fit
        d_us = _eval_piecewise(xs, centers, coefs)
        t_fit[order] = t0 + 1000.0 * (e - esp0) + d_us * 1e3
        valid[order] = True
        epochs.append(EpochModel(boot=int(boot[a]), esp_lo=float(e[0]), esp_hi=float(e[-1]),
                                 esp0=float(esp0), t0_ns=t0,
                                 centers_s=centers, coef=coefs))
        slopes.append(float(coefs[:, 0].mean()))
    resid = np.where(valid, t.astype(np.float64) - t_fit, np.nan)
    rep = FitReport(t_fit=t_fit, resid_ns=resid, valid=valid, slopes=slopes)
    return BoardClockModel(epochs), rep


def wrap_continuity(esp_us, resid_ns, valid, *, halfwin_s=30.0):
    """Median residual difference before/after wrap boundary (k*2^32 us) within +-halfwin -- <1ms means unwrap verification passes.

    If esp was reset due to reboot, there are no samples at the boundary (reported as 'insufficient samples' -- normal).
    """
    esp = np.asarray(esp_us, np.float64)
    out = []
    if len(esp) == 0:
        return out
    for k in range(int(esp.min() // WRAP_US) + 1, int(esp.max() // WRAP_US) + 1):
        w = k * float(WRAP_US)
        left = valid & (esp >= w - halfwin_s * 1e6) & (esp < w)
        right = valid & (esp >= w) & (esp < w + halfwin_s * 1e6)
        if left.sum() < 10 or right.sum() < 10:
            out.append({"wrap_at_min": w / 6e7, "delta_ms": None, "ok": False,
                        "note": "insufficient samples"})
            continue
        d = abs(float(np.nanmedian(resid_ns[left])) - float(np.nanmedian(resid_ns[right])))
        out.append({"wrap_at_min": w / 6e7, "delta_ms": d / 1e6, "ok": bool(d < 1e6)})
    return out
