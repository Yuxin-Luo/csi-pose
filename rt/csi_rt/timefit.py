"""Causal time reconstruction — bridge chunk stamps (same t_ns run ~11pkt·period ~105ms measured).

t_hat = esp_ns + offset(RX-specific rolling-min) — min = 'least late' sample (chunk end frame).
't_ns direct use' rejected by measurement — this module is the reserved fallback."""
from collections import deque


class CausalOffset:
    """RX single-unit esp->host offset rolling-min estimator. Reset on boot change."""

    def __init__(self, *, window_ns=3_000_000_000):
        self._w = window_ns
        self._d = deque()                            # (t_ns, off) — off monotonic min-deque
        self._boot = None

    def update(self, boot_id, t_ns, esp_ns):
        """Observe 1 packet -> current offset(ns). Usable from first packet (warmup)."""
        if self._boot is not None and boot_id != self._boot:
            self._d.clear()                          # Reboot — esp clock restarted
        self._boot = boot_id
        off = t_ns - esp_ns
        # Equal offset: keep new (late t_ns) sample — maximize window retention (rolling-min accuracy)
        while self._d and self._d[-1][1] >= off:     # Maintain min-deque
            self._d.pop()
        self._d.append((t_ns, off))
        while self._d and self._d[0][0] < t_ns - self._w:
            self._d.popleft()
        return self._d[0][1]

    def estimate(self, boot_id, t_ns, esp_ns):
        """update + t_hat output."""
        return esp_ns + self.update(boot_id, t_ns, esp_ns)
