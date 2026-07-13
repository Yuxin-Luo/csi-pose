"""Overlay rendering — canvas only, [video+skeleton | canvas] side-by-side when --video.

Joint order = teacher BODY-18 (coco17_to_body18 output order). Edges are OpenPose BODY-18
standard 18 connections — edge constant not in teacher, defined here once (no double-definition)."""
import cv2
import numpy as np

CANVAS_WH = (640, 480)
C_MIN = 0.3
EDGES = ((1, 2), (2, 3), (3, 4), (1, 5), (5, 6), (6, 7), (1, 8), (8, 9), (9, 10),
         (1, 11), (11, 12), (12, 13), (1, 0), (0, 14), (14, 16), (0, 15), (15, 17))


def project(xy_norm, W, H):
    xy = np.nan_to_num(np.asarray(xy_norm, np.float32), nan=0.0, posinf=1.0, neginf=0.0)
    px = xy * [W, H]
    return np.clip(px, [0, 0], [W - 1, H - 1]).astype(np.int32)


def _draw_skeleton(img, xy, c, W, H):
    xy = np.asarray(xy, np.float32)
    finite = np.isfinite(xy).all(axis=1)  # Per-joint coordinate NaN/Inf gate — block corner points/lines
    px = project(xy, W, H)
    for a, b in EDGES:
        if finite[a] and finite[b] and c[a] >= C_MIN and c[b] >= C_MIN:
            cv2.line(img, tuple(px[a]), tuple(px[b]), (0, 200, 0), 2)
    for j in range(len(px)):
        if finite[j] and c[j] >= C_MIN:
            g = int(np.clip(c[j], 0, 1) * 255)
            cv2.circle(img, tuple(px[j]), 4, (0, g, 255 - g), -1)


def _banner(img, present, fall_state, hud):
    # Combined with fall.py FSM state vocabulary (IDLE/IMPACT/ALARM) — undefined state = KeyError (fail-loud)
    color = {"IDLE": (80, 80, 80), "IMPACT": (0, 160, 255),
             "ALARM": (0, 0, 230)}[fall_state]
    cv2.rectangle(img, (0, 0), (img.shape[1], 28), color, -1)
    tag = " RANDOM" if hud["random"] else (" DIAG" if hud.get("diag") else "")
    cv2.putText(img, f"{'PRESENT' if present else 'ABSENT'} | {fall_state}{tag}",
                (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
    txt = (f"fps {hud['fps']:.1f}  infer {hud['infer_ms']:.1f}ms  "
           f"e2e {hud['e2e_ms']:.0f}ms  drop {hud['drop']}  motion {hud['motion']:.2f}")
    cv2.putText(img, txt, (8, img.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX,
                0.45, (200, 200, 200), 1)


def render(video_frame, xy_norm, c, *, present, fall_state, hud):
    """video_frame=None -> canvas only. ndarray -> [video+skeleton | canvas] horizontal concat.

    hud required 6 keys: fps·infer_ms·e2e_ms·drop·motion·random. hud['diag'] (bool) is optional
    and is consumed only for the banner tag — skeleton logic is unchanged."""
    W, H = CANVAS_WH
    canvas = np.zeros((H, W, 3), np.uint8)
    if present and xy_norm is not None:
        _draw_skeleton(canvas, xy_norm, c, W, H)
        if hud.get("diag"):
            # dev_doc/21 §5: diag-mode skeleton is a placeholder — draw a giant diagonal stripe
            # so the user can't mistake it for a real prediction. Always under the skeleton.
            cv2.line(canvas, (0, 0), (W - 1, H - 1), (120, 120, 120), 1)
            cv2.putText(canvas, "PRED-INVALID: --diag-fill-missing placeholder",
                        (8, H - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (120, 120, 120), 1)
    _banner(canvas, present, fall_state, hud)
    if video_frame is None:
        return canvas
    vf = cv2.resize(video_frame, (W, H))
    if present and xy_norm is not None:
        _draw_skeleton(vf, xy_norm, c, W, H)
        if hud.get("diag"):
            cv2.line(vf, (0, 0), (W - 1, H - 1), (120, 120, 120), 1)
            cv2.putText(vf, "PRED-INVALID: --diag-fill-missing placeholder",
                        (8, H - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (120, 120, 120), 1)
    return np.hstack([vf, canvas])
