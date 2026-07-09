"""Replay video — nearest /video/t_ns frame (cam-csi correction applied).

Sequential access前提 (t monotonic increasing) — VideoCapture only advances forward."""
import json
from pathlib import Path

import cv2
import h5py
import numpy as np


class ReplayVideo:
    def __init__(self, h5_path, mp4_path, pairing_json):
        with h5py.File(h5_path, "r") as h:
            if "video" not in h:
                raise SystemExit(f"/video not found: {h5_path} — --video only available for camera-recorded sessions")
            self._t = h["video/t_ns"][...].astype(np.int64)
            self._idx = h["video/frame_idx"][...].astype(np.int64)
        shift = 0.0
        if pairing_json:
            d = json.loads(Path(pairing_json).read_text(encoding="utf-8"))
            shift = (d["cam_correction_ms"] - d["csi_correction_ms"]) * 1e6
        self._shift_ns = int(shift)                  # t' = t + (cam-csi)
        self._cap = cv2.VideoCapture(str(mp4_path))
        if not self._cap.isOpened():
            raise SystemExit(f"Failed to open mp4: {mp4_path}")
        self._pos = -1
        self._frame = None

    def frame_for(self, t_ns):
        want = int(np.clip(np.searchsorted(self._t, t_ns + self._shift_ns),
                           0, len(self._t) - 1))
        if want > 0 and abs(self._t[want - 1] - (t_ns + self._shift_ns)) \
                <= abs(self._t[want] - (t_ns + self._shift_ns)):
            want -= 1
        target = int(self._idx[want])
        # Clock backward (target<_pos) = keep previous frame (demo monotonic clock contract — no rewind)
        while self._pos < target:
            ok, f = self._cap.read()
            if not ok:
                # After video end, t = last frame fixed (intentional)
                break
            self._pos += 1
            self._frame = f
        return self._frame
