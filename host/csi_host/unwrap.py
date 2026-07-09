class TimeUnwrapper:
    """Restore esp_timer_us (u32, 71.58 min wrap) to monotonic time per board.

    update() returns: (unwrapped_us, event)  event in {"", "wrap", "reboot"}.
    boot_id change = reboot -> epoch reset (distinguished from wrap accumulation — clock fit also resets).
    """
    WRAP = 1 << 32

    def __init__(self):
        self.boot_id = None
        self.last_raw = None
        self.epoch = 0
        self.wraps = 0
        self.reboots = 0

    def update(self, *, boot_id: int, t_us: int):
        event = ""
        if self.boot_id is None:
            self.boot_id = boot_id
        elif boot_id != self.boot_id:
            self.boot_id, self.epoch, self.last_raw = boot_id, 0, None
            self.reboots += 1
            event = "reboot"
        if self.last_raw is not None and t_us < self.last_raw:
            self.epoch += 1
            self.wraps += 1
            event = "wrap"
        self.last_raw = t_us
        return self.epoch * self.WRAP + t_us, event
