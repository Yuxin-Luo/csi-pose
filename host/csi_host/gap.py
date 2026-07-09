class LinkTracker:
    """Track seq gaps per link (rx_i, tx_j) (RF loss vs transmission loss diagnostic).

    update(seq) returns: None (normal) | "gap" (loss counted) | "reset" (seq rolled back --
    TX reboot or START restart, loss not counted, baseline reset).
    """

    def __init__(self):
        self.received = 0
        self.lost = 0
        self.resets = 0
        self._last = None

    def update(self, seq: int):
        event = None
        if self._last is not None:
            if seq > self._last + 1:
                self.lost += seq - self._last - 1
                event = "gap"
            elif seq <= self._last:
                self.resets += 1
                event = "reset"
        self.received += 1
        self._last = seq
        return event

    def rebaseline(self):
        """Next update is the new baseline -- prevents RX reboot gap from being mistaken as RF loss."""
        self._last = None

    @property
    def loss_ratio(self) -> float:
        total = self.received + self.lost
        return self.lost / total if total else 0.0
