# """pose18 → PAM (4,18,18).
# Diagonal: (x_r, y_r, c_r, c_r), off-diagonal: (x_r−x_c, y_r−y_c, c_r·c_c, c_r·c_c).
# Coordinates normalized to x/W, y/H before storage (design §7)."""
# Translation: pose18 → PAM (4,18,18). Diagonal (x_r, y_r, c_r, c_r), off-diagonal (x_r−x_c, y_r−y_c, c_r·c_c, c_r·c_c). Coordinates normalized to x/W, y/H before storage (design §7).
import json
from pathlib import Path

import h5py
import numpy as np

from .labels import STATUS_MULTI, STATUS_OK


def pam_from_pose18(pose18, *, W, H):
    """pose18 (18,3) → PAM (4,18,18) float32.

    Missing/NaN frames are handled by caller (build_pam) as presence=0 → Y=0; this function propagates NaN as-is."""
    # """pose18 (18,3) → PAM (4,18,18) float32.
    # Missing/NaN frames are handled by caller (build_pam) as presence=0 → Y=0; this function propagates NaN as-is."""
    p = np.asarray(pose18, np.float32)
    if p.shape != (18, 3):
        raise ValueError(f"Not BODY-18 shape: {p.shape}")
    x = p[:, 0] / float(W)
    y = p[:, 1] / float(H)
    c = p[:, 2]
    Y = np.empty((4, 18, 18), np.float32)
    Y[0] = x[:, None] - x[None, :]
    Y[1] = y[:, None] - y[None, :]
    Y[2] = c[:, None] * c[None, :]
    Y[3] = Y[2]
    d = np.arange(18)
    Y[0, d, d] = x
    Y[1, d, d] = y
    Y[2, d, d] = c
    Y[3, d, d] = c
    return Y


def build_pam(h5_path, *, verdicts=None, force=False, say=print):
    """QA-applied finalization: /samples/Y·presence·label_ok + /labels/qa_fail.

    presence: person exists (ok) — no_person is 0 but used as negative sample in training.
    label_ok: usable for training — multi (v1 discard) and QA fail are 0. Discard ≠ negative."""
    # """QA-applied finalization: /samples/Y·presence·label_ok + /labels/qa_fail.
    # presence: person exists (ok) — no_person is 0 but used as negative sample in training.
    # label_ok: usable for training — multi (v1 discard) and QA fail are 0. Discard ≠ negative."""
    with h5py.File(h5_path, "r+") as h:
        if "labels" not in h or "pose18" not in h["labels"]:
            raise SystemExit("/labels missing — run teacher.py label --h5 first")
        if "samples" not in h or "t_ns" not in h["samples"]:
            raise SystemExit("/samples missing — run host csi_pipe samples build first")
        g = h["labels"]
        pose18 = g["pose18"][...]
        status = g["status"][...]
        W, H = int(g.attrs["W"]), int(g.attrs["H"])
        F = len(status)
        qa_fail = np.zeros(F, bool)
        if verdicts:
            vd = json.loads(Path(verdicts).read_text(encoding="utf-8"))
            vd.pop("_total", None)                     # qa.exp() completeness meta — not a verdict
            for k, v in vd.items():
                f = int(k)
                if f >= F:
                    raise SystemExit(f"Verdict frame {f} ≥ F {F} — verdicts from different session?")
                if v == "fail":
                    qa_fail[f] = True
        if "samples/Y" in h or "labels/qa_fail" in h:
            if not force:
                raise SystemExit("Existing pam output (/samples/Y·/labels/qa_fail) exists — rebuild with --force")
            # Rebuild is non-atomic (no temp group) — Y deleted first, so existence guard catches
            # interrupts, re-run is self-recovering. qa_fail alone (= after host build --force)
            # included — without it, create_dataset would conflict below
            for k in ("samples/Y", "samples/presence", "samples/label_ok",
                      "labels/qa_fail"):
                if k in h:
                    del h[k]
        vt = h["video/t_ns"][...].astype(np.int64)
        fi = (h["video/frame_idx"][...].astype(np.int64) if "video/frame_idx" in h
              else np.arange(len(vt), dtype=np.int64))     # Legacy session identity fallback
        order = np.argsort(vt, kind="stable")
        vt_s, fi_s = vt[order], fi[order]
        st = h["samples/t_ns"][...].astype(np.int64)
        rows = np.searchsorted(vt_s, st)
        bad = (rows >= len(vt_s)) | (vt_s[np.minimum(rows, len(vt_s) - 1)] != st)
        if bad.any():
            raise SystemExit(
                f"{int(bad.sum())} anchors not in /video/t_ns — session mismatch between build and label")
        frames = fi_s[rows]
        if len(frames) and int(frames.max()) >= F:
            raise SystemExit(f"frame_idx {int(frames.max())} ≥ label F {F} — mp4/session mismatch")
        sf = status[frames]
        qf = qa_fail[frames]
        presence = (sf == STATUS_OK) & ~qf
        label_ok = ~qf & (sf != STATUS_MULTI)
        N = len(frames)
        Y = np.zeros((N, 4, 18, 18), np.float16)
        for n in np.flatnonzero(presence):
            Y[n] = pam_from_pose18(pose18[frames[n]], W=W, H=H).astype(np.float16)
        sg = h["samples"]
        sg.create_dataset("Y", data=Y)
        sg.create_dataset("presence", data=presence)
        sg.create_dataset("label_ok", data=label_ok)
        g.create_dataset("qa_fail", data=qa_fail)
        sg.attrs["pam_build"] = json.dumps(
            {"verdicts": str(verdicts) if verdicts else None, "N": N,
             "presence": int(presence.sum()), "discard": int((~label_ok).sum())},
            ensure_ascii=False)
        say(f"PAM N={N} presence={int(presence.sum())} discarded={int((~label_ok).sum())}")
        return {"N": N, "presence": int(presence.sum()),
                "discarded": int((~label_ok).sum())}
