"""Presence gate + model-free motion energy (safety net, fall combination).

Gate v1 = mean(c_hat) >= tau alone. Motion energy = std of recent window_s packets'
average amplitude per link -> 9-link average (not using presence judgment — for HUD and fall input)."""
from collections import deque

import numpy as np


class PresenceGate:
    def __init__(self, tau, *, force=False):
        self.tau, self.force = float(tau), bool(force)

    def update(self, c):
        return True if self.force else bool(np.mean(c) >= self.tau)


class MotionEnergy:
    def __init__(self, *, window_s=0.5):
        self._w_ns = int(window_s * 1e9)
        self._d = {}

    def add(self, rx, tx, t_ns, amp56):
        d = self._d.setdefault((rx, tx), deque())
        d.append((t_ns, float(np.mean(amp56))))
        while d and d[0][0] < t_ns - self._w_ns:
            d.popleft()

    def energy(self):
        stds = [np.std([v for _, v in d]) for d in self._d.values() if len(d) >= 2]
        return float(np.mean(stds)) if stds else 0.0
