"""M1.5 minicapture protocol — plan/segment state machine.

Stdlib only — runs on Windows console (tools/m15_protocol.py), no numpy/h5py dependency.
Grid 1-9 = TX-side→RX-side row-major, mat position 5 fixed. Capture-2 traversal uses a fixed
shuffle (blocks leak where time drift within a capture masquerades as positional signal)."""

PLAN_VERSION = "m15-v1"
ORDERS = {1: (1, 2, 3, 4, 5, 6, 7, 8, 9), 2: (5, 1, 8, 3, 7, 2, 9, 4, 6)}


def plan_segments(capture):
    """capture number (1|2) → 13 segments [{idx,pos,posture,empty,hint_s}]."""
    if capture not in ORDERS:
        raise SystemExit(f"capture must be 1|2 — got {capture}")
    segs = [{"pos": None, "posture": None, "empty": True, "hint_s": 60}]
    segs += [{"pos": p, "posture": "stand", "empty": False, "hint_s": 40}
             for p in ORDERS[capture]]
    segs += [{"pos": 5, "posture": "sit", "empty": False, "hint_s": 40},
             {"pos": 5, "posture": "lie", "empty": False, "hint_s": 60},
             {"pos": None, "posture": None, "empty": True, "hint_s": 60}]
    return [{"idx": i, **s} for i, s in enumerate(segs)]


class M15Session:
    """Boundary key events → segment finalization. mark toggle: moving→settled (start) / settled→moving (end).

    undo = cancel last boundary 1 (repeatable): cancel start → return to moving,
    cancel end → resume. Even if done, undo can revert the last boundary (mistake
    of pressing end too quickly). After abort, mark/undo are not allowed (terminal state) —
    finalized boundaries are preserved.
    Timestamps are injected by caller (time.time_ns) — non-increasing raises ValueError
    (clock anomaly defense, CLI catches and ignores). result() is only available in
    completed/aborted state — unfinished (open) segments are discarded.
    """

    def __init__(self, capture, session):
        self.capture = capture
        self.session = session
        self.plan = plan_segments(capture)
        self.k = 0                # current (unfinalized) segment index
        self.settled = False      # True = start boundary recorded (in progress)
        self._open_t = None
        self.bounds = []          # finalized [(t_start, t_end)] — corresponds to plan[i] index
        self.aborted = False

    @property
    def done(self):
        return self.k >= len(self.plan)

    def current(self):
        return None if self.done else self.plan[self.k]

    def _last_t(self):
        if self.settled:
            return self._open_t
        return self.bounds[-1][1] if self.bounds else 0

    def mark(self, t_ns):
        if self.aborted:
            raise ValueError("aborted — no further operations allowed")
        if self.done:
            raise ValueError("plan ended — further mark not allowed")
        if t_ns <= self._last_t():
            raise ValueError(f"time regression: {t_ns} ≤ {self._last_t()}")
        if not self.settled:
            self._open_t = t_ns
            self.settled = True
            return "start"
        self.bounds.append((self._open_t, t_ns))
        self._open_t = None
        self.settled = False
        self.k += 1
        return "end"

    def undo(self):
        if self.aborted:
            raise ValueError("aborted — no further operations allowed")
        if self.settled:
            self.settled = False
            self._open_t = None
            return "start_cancelled"
        if self.bounds:
            start, _ = self.bounds.pop()
            self.k -= 1
            self.settled = True
            self._open_t = start
            return "end_cancelled"
        return None

    def abort(self):
        self.aborted = True
        self.settled = False
        self._open_t = None

    def result(self):
        if not (self.done or self.aborted):
            raise ValueError("not completed or aborted — result unavailable (call after done or abort)")
        segments = []
        for i, (s, e) in enumerate(self.bounds):
            p = self.plan[i]
            segments.append({"idx": p["idx"], "pos": p["pos"],
                             "posture": p["posture"], "empty": p["empty"],
                             "t_start_ns": s, "t_end_ns": e})
        return {"session": self.session, "capture": self.capture,
                "plan_version": PLAN_VERSION, "segments": segments,
                "aborted": self.aborted}
