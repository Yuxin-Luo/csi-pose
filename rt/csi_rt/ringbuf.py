"""Causal grid/window cutting — reuse batch align primitives.

Buffer only has t<=T packets -> grid_block's 'tb > s.t[-1] -> mask' is exactly the causal constraint.
Therefore rt valid is a subset of batch valid (tail truncation repair is structurally guaranteed).
boot_id change = reboot -> clear that link buffer (no interpolation across epoch boundary)."""
from collections import deque
from dataclasses import dataclass

import numpy as np

from csi_pipe.align import LinkStream, cut_windows, fill_gaps, grid_block
from csi_pipe.samples import STEP_NS

LINKS = [(r, t) for r in range(3) for t in range(3)]
WIN_NS = 5 * STEP_NS


@dataclass(frozen=True, eq=False)
class Pkt:
    rx: int
    tx: int
    boot_id: int
    t_ns: int
    seq: int
    amp: np.ndarray            # (56,) f32 — amplitude(iq) product


@dataclass(frozen=True, eq=False)
class CutResult:
    X: np.ndarray              # (280,3,3) f16 — same convention as batch cut_windows
    valid: bool
    bad: np.ndarray            # (3,3) int — per-link mask slot count


class RingBuf:
    def __init__(self, *, horizon_ns=1_200_000_000):
        self._links = {lk: deque() for lk in LINKS}
        self._boot = {}
        self._h = horizon_ns

    def add(self, p: Pkt):
        d = self._links[(p.rx, p.tx)]
        prev = self._boot.get((p.rx, p.tx))
        if prev is not None and prev != p.boot_id:
            d.clear()
        self._boot[(p.rx, p.tx)] = p.boot_id
        if d and (p.t_ns <= d[-1].t_ns or p.seq <= d[-1].seq):
            return  # Non-monotonic (same boot seq backward) = glitch/retransmit — discard (real TX restart has boot_id change -> clear)
        d.append(p)
        while d and d[0].t_ns < p.t_ns - self._h:
            d.popleft()

    def cut(self, end_ns: int) -> CutResult:
        tb = np.arange(end_ns - WIN_NS, end_ns, STEP_NS, dtype=np.int64)
        amp_blk = np.zeros((5, 56, 3, 3), np.float32)
        mask_blk = np.zeros((5, 3, 3), bool)
        for rx, tx in LINKS:
            d = self._links[(rx, tx)]
            if len(d) < 2:                           # grid_block prerequisite (n>=2) not met
                mask_blk[:, rx, tx] = True
                continue
            t = np.fromiter((p.t_ns for p in d), np.int64, len(d))
            seq = np.fromiter((p.seq for p in d), np.int64, len(d))
            amp = np.stack([p.amp for p in d])
            t2, a2, m2, br = fill_gaps(t, seq, amp)
            a, m = grid_block(LinkStream(t=t2, amp=a2, interp=m2, breaks=br), tb)
            # Clamp (+1ns) pair: if it becomes extrapolation denominator, amplitude explodes (f16 overflow)
            # Physical upper limit (int8 iq max |127+127j|~179.6 < 256, normal/batch paths unaffected)
            amp_blk[:, :, rx, tx], mask_blk[:, rx, tx] = np.clip(a, 0.0, 256.0), m
        X, valid = cut_windows(amp_blk, mask_blk, np.array([0]))
        return CutResult(X=X[0], valid=bool(valid[0]),
                         bad=mask_blk.sum(axis=0).astype(int))
