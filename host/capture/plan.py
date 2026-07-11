"""Plan parser + segment state + overlay renderer for cam_capture.

Pure functions: no MQTT / no serial / no cv2 at import-time.
cv2 imported lazily inside draw_overlay so unit-imports stay light.
"""
from dataclasses import dataclass
from typing import List, Optional, Tuple


def parse_plan(s: str) -> List[Tuple[int, str, int]]:
    """Parse "1:label:60,2:label:40,..." -> [(1,"label",60), ...]."""
    out = []
    for seg in s.split(","):
        parts = seg.strip().split(":")
        if len(parts) != 3:
            raise ValueError(f"malformed plan segment: {seg!r}")
        idx, label, dur = int(parts[0]), parts[1].strip(), int(parts[2])
        out.append((idx, label, dur))
    return out


@dataclass
class PlanState:
    plan: list
    cur_seg: int = 0
    cur_label: str = ""
    seg_start: Optional[float] = None

    def __post_init__(self):
        self.cur_label = self.plan[0][1]

    def tick(self, now: float) -> bool:
        if self.seg_start is None or self.cur_seg >= len(self.plan) - 1:
            return False
        _, _, dur = self.plan[self.cur_seg]
        if now - self.seg_start >= dur:
            self.cur_seg += 1
            self.cur_label = self.plan[self.cur_seg][1]
            self.seg_start = now
            return True
        return False

    @property
    def total_segments(self) -> int:
        return len(self.plan)

    @property
    def cur_duration(self) -> int:
        return self.plan[self.cur_seg][2]


def draw_overlay(frame, state: PlanState, elapsed_sec: float):
    """Draw segment overlay in upper-right corner (in-place)."""
    import cv2
    h, w = frame.shape[:2]
    line1 = f"Segment {state.cur_seg + 1}/{state.total_segments} — {state.cur_label}"
    line2 = f"● RECORDING  {elapsed_sec:.1f}s / {state.cur_duration}s"
    font, scale, thick, pad = cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1, 8
    sizes = [cv2.getTextSize(t, font, scale, thick)[0] for t in (line1, line2)]
    box_w = max(s[0] for s in sizes) + 2 * pad
    box_h = sum(s[1] for s in sizes) + 3 * pad
    x0, y0 = w - box_w - 10, 10
    cv2.rectangle(frame, (x0, y0), (x0 + box_w, y0 + box_h), (0, 255, 255), -1)
    y = y0 + pad + sizes[0][1]
    for txt, (tw, th) in zip((line1, line2), sizes):
        cv2.putText(frame, txt, (x0 + pad, y), font, scale, (0, 0, 0), thick, cv2.LINE_AA)
        y += th + pad
    return frame