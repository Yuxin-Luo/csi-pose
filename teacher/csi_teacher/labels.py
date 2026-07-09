"""Frame status policy + /labels·sidecar(.labels.npz) I/O."""
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import h5py
import numpy as np

from .body18 import coco17_to_body18

STATUS_OK, STATUS_NO_PERSON, STATUS_MULTI = 0, 1, 2


@dataclass
class LabelResult:
    pose18: np.ndarray          # (F,18,3) f32 — missing data as NaN
    status: np.ndarray          # (F,) u8
    det_score: np.ndarray       # (F,) f32 — no_person/multi as NaN
    W: int
    H: int
    attrs: dict = field(default_factory=dict)


def label_frame(runner, frame, det_thr):
    """Single-frame policy: boxes with det_thr or higher -> 0=no_person, 1=ok(pose), >=2=multi(discard)."""
    dets = np.asarray(runner.detect(frame), np.float32).reshape(-1, 5)
    dets = dets[dets[:, 4] >= det_thr]
    if len(dets) == 0:
        return STATUS_NO_PERSON, None, np.nan
    if len(dets) > 1:
        return STATUS_MULTI, None, np.nan
    kpts17 = runner.pose(frame, dets[0, :4])
    return STATUS_OK, coco17_to_body18(kpts17), float(dets[0, 4])


def run_label(frames, runner, *, det_thr, progress=None):
    """Frame iterable -> LabelResult. (W,H) from the first frame."""
    pose, stat, score = [], [], []
    W = H = None
    for i, frame in enumerate(frames):
        # Fixed-camera assumption — W/H latch from first frame (variable resolution not supported)
        if W is None:
            H, W = frame.shape[:2]
        s, p18, ds = label_frame(runner, frame, det_thr)
        stat.append(s)
        score.append(ds)
        pose.append(p18 if p18 is not None else np.full((18, 3), np.nan, np.float32))
        if progress and (i + 1) % 200 == 0:
            progress(i + 1)
    if W is None:
        raise SystemExit("0 frames — mp4 is empty or decode failed")
    return LabelResult(np.stack(pose), np.asarray(stat, np.uint8),
                       np.asarray(score, np.float32), W, H)


def base_attrs(*, mp4, det_thr, det_model, pose_model):
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        rev = ""
    return {"mp4": str(Path(mp4).resolve()), "det_thr": float(det_thr), "det_model": det_model,
            "pose_model": pose_model, "git_rev": rev or "unknown"}


def save_npz(path, res):
    np.savez_compressed(path, pose18=res.pose18, status=res.status,
                        det_score=res.det_score, W=res.W, H=res.H,
                        attrs=json.dumps(res.attrs, ensure_ascii=False))


def load_npz(path):
    z = np.load(path, allow_pickle=False)
    return LabelResult(z["pose18"], z["status"], z["det_score"],
                       int(z["W"]), int(z["H"]), json.loads(str(z["attrs"])))


def check_video_mapping(h, F, *, warn):
    """mp4 frame count F vs /video — if frame_idx exists, allow missing frames; otherwise enforce match."""
    if "video/t_ns" not in h:
        raise SystemExit("/video/t_ns missing — not a session HDF5 (use --h5 for mp4-only)")
    V = h["video/t_ns"].shape[0]
    if "video/frame_idx" in h:
        fi = h["video/frame_idx"][...]
        if len(fi) != V:
            raise SystemExit(f"/video corrupted: t_ns {V} != frame_idx {len(fi)}")
        if V and int(fi.max()) >= F:
            raise SystemExit(f"frame_idx max {int(fi.max())} >= mp4 frames {F} — mismatched session?")
        if V > F:
            raise SystemExit(f"/video {V} > mp4 {F} — mismatched session?")
        if V < F:
            warn(f"cam/meta loss suspected: /video {V} < mp4 {F} — proceeding with frame_idx mapping")
    elif V != F:
        raise SystemExit(f"/video/t_ns {V} != mp4 frames {F} — sessions without frame_idx must match exactly")


def write_h5(h5_path, res, *, force=False, warn=print):
    with h5py.File(h5_path, "r+") as h:
        check_video_mapping(h, len(res.status), warn=warn)
        if "labels" in h and not force:
            raise SystemExit("Existing /labels exists — use --force to relabel")
        if "labels_tmp" in h:
            del h["labels_tmp"]                  # Previous failed-run residue cleanup
        g = h.create_group("labels_tmp")
        g.create_dataset("pose18", data=res.pose18.astype(np.float32))
        g.create_dataset("status", data=res.status.astype(np.uint8))
        g.create_dataset("det_score", data=res.det_score.astype(np.float32))
        g.attrs["W"] = int(res.W)
        g.attrs["H"] = int(res.H)
        g.attrs["F"] = int(len(res.status))
        for k, v in res.attrs.items():
            g.attrs[k] = v
        if "labels" in h:                        # Replace only after full success
            del h["labels"]
        h.move("labels_tmp", "labels")
