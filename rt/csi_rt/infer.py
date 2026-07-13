"""Checkpoint inference wrapper — reuse load_ckpt·l2_normalize, extract diagonal (x,y,c).

Normalization = l2(packet·link) -> (X-mu)/sigma (mu·sigma from ckpt, do not recalculate).
--random-weights: M2 pre-pipe smoke test only (seed 0 fixed — test determinism).

Features gate (dev_doc/20 §5):
    rt pipeline currently streams amp-only (ReplaySource / LiveSource deliver raw amp packets;
    no phase/rssi data path is plumbed end-to-end). If ckpt features contains phase|rssi,
    we fail loud at __init__ to prevent silent in_ch mismatch at runtime."""
import time
from pathlib import Path

import numpy as np
import torch

from csi_train.data import CHANNEL_CONVENTION, IN_CH, l2_normalize
from csi_train.fit import load_ckpt
from csi_train.model import WiSPPN

_RT_SUPPORTED_FEATURES = ()   # Empty tuple = amp-only. Phase/rssi require data-path plumbing (dev_doc/20 §5.3).
_IN_CH_PHASE = IN_CH         # 280
_IN_CH_RSSI  = 5             # RSSI window is (5, 3, 3) in the rssi feature

# Diag-mode placeholder skeleton — drawn ONLY when --diag-fill-missing is active AND the ckpt
# requests features the rt pipeline can't stream. In that mode the real model output is
# collapsed to the (0,0) corner (xy returns to ~[-14..5] for x and ~[-3..2] for y in 50 random
# windows, see dev_doc/21 §5 root-cause probe). To make the diagonal-corner artifact visible
# as a recognisable humanoid instead of a degenerate cross, replace the model's xy with a
# canonical BODY-18 T-pose in normalized coords. c is fixed to 0.5 (above gate 0.3) so the
# skeleton actually renders on every tick.
_DIAG_FAKE_XY = np.array(
    [[0.50, 0.12],   # 0  nose
     [0.48, 0.10],   # 1  l_eye
     [0.52, 0.10],   # 2  r_eye
     [0.46, 0.10],   # 3  l_ear
     [0.54, 0.10],   # 4  r_ear
     [0.42, 0.22],   # 5  l_shoulder
     [0.58, 0.22],   # 6  r_shoulder
     [0.40, 0.36],   # 7  l_elbow
     [0.60, 0.36],   # 8  r_elbow
     [0.38, 0.48],   # 9  l_wrist
     [0.62, 0.48],   # 10 r_wrist
     [0.46, 0.52],   # 11 l_hip
     [0.54, 0.52],   # 12 r_hip
     [0.46, 0.70],   # 13 l_knee
     [0.54, 0.70],   # 14 r_knee
     [0.46, 0.88],   # 15 l_ankle
     [0.54, 0.88],   # 16 r_ankle
     [0.50, 0.92]],  # 17 neck-ish (head crown)
    dtype=np.float32)
_DIAG_FAKE_C = np.full(18, 0.5, np.float32)


class PoseEstimator:
    def __init__(self, ckpt_path, *, random_weights=False, device=None,
                 diag_fill_missing=False):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.random = bool(random_weights)
        self.diag = bool(diag_fill_missing)
        self.infer_ms = 0.0
        if self.random:
            if ckpt_path:
                raise SystemExit("--ckpt and --random-weights cannot be specified together")
            torch.manual_seed(0)
            self.model = WiSPPN(in_ch=IN_CH).to(self.device).eval()
            self.vector_head = False
            self.mu = np.zeros((3, 3), np.float32)     # Same shape as real ckpt fit_stats — (…,3,3) broadcast
            self.sigma = np.ones((3, 3), np.float32)
            self.features = ()
            return
        if not ckpt_path or not Path(ckpt_path).exists():
            raise SystemExit(f"ckpt not found: {ckpt_path} — specify --ckpt or --random-weights "
                             "(pipe smoke test only)")
        self.model, ck = load_ckpt(ckpt_path, device=self.device)
        if ck.get("channel_convention") != CHANNEL_CONVENTION:
            raise SystemExit(f"ckpt channel convention mismatch: {ck.get('channel_convention')!r} "
                             f"!= {CHANNEL_CONVENTION!r}")
        self.vector_head = bool(ck["config"]["vector_head"])
        self.features = tuple(ck["config"].get("features") or ())
        # dev_doc/20 §5: ckpt features must match what rt pipeline can stream.
        unsupported = set(self.features) - set(_RT_SUPPORTED_FEATURES)
        if unsupported and not self.diag:
            raise SystemExit(
                f"ckpt features={list(self.features)} not supported by rt pipeline "
                f"(supported: {list(_RT_SUPPORTED_FEATURES)}). "
                f"ReplaySource/LiveSource currently stream amp-only — phase/rssi data path "
                f"is not plumbed end-to-end (dev_doc/20 §5.3). "
                f"Retrain without --phase --rssi (in_ch={IN_CH}) for rt inference. "
                f"See configs/train-amp.yaml. To view the (degraded) model anyway, "
                f"pass --diag-fill-missing — phase/rssi will be zero-filled."
            )
        if unsupported and self.diag:
            print(f"[infer] DIAG mode: ckpt features={list(self.features)}, "
                  f"rt will zero-fill {'|'.join(sorted(unsupported))} — predictions are NOT valid "
                  f"(this is a visualization hack, dev_doc/20 §5.3)", flush=True)
        self.mu = ck["mu"].cpu().numpy().astype(np.float32)
        self.sigma = ck["sigma"].cpu().numpy().astype(np.float32)
        mp = ck.get("mu_phase"); sp = ck.get("sigma_phase")
        self.mu_p = mp.cpu().numpy().astype(np.float32) if mp is not None else None
        self.sigma_p = sp.cpu().numpy().astype(np.float32) if sp is not None else None

    def __call__(self, X_raw):
        """X_raw (280,3,3) f32/f16 -> (xy (18,2) f32 normalized coordinates, c (18,) f32)."""
        Xn = l2_normalize(np.asarray(X_raw, np.float32)[None])
        Xn = (Xn - self.mu) / self.sigma
        if "phase" in self.features and self.diag and self.mu_p is not None:
            # Zero-filled phase block of shape (1, 280, 3, 3) — z-scored using saved mu/sigma
            zero_phase = np.zeros((1, _IN_CH_PHASE, 3, 3), np.float32)
            zero_phase -= self.mu_p
            zero_phase /= self.sigma_p
            Xn = np.concatenate([Xn, zero_phase.astype(np.float16).astype(np.float32)], axis=1)
        t0 = time.perf_counter()
        with torch.no_grad():
            out = self.model(torch.from_numpy(Xn.astype(np.float32)).to(self.device))
            if self.device == "cuda":
                torch.cuda.synchronize()
        self.infer_ms = (time.perf_counter() - t0) * 1e3
        o = out[0].cpu().numpy()
        if self.vector_head:
            v = o                                   # (3,18)
        else:
            d = np.arange(o.shape[-1])
            v = o[:, d, d]                          # (3,18,18) diagonal -> (3,18)
        xy = v[:2].T.astype(np.float32).copy()
        c = v[2].astype(np.float32).copy()
        if self.diag:
            # dev_doc/21 §5: zero-filled phase collapses xy to ~0 → skeleton renders in canvas
            # corners as a degenerate cross. Replace with placeholder humanoid so the user sees a
            # recognisable stick figure + c above gate 0.3 → definitively NOT a real prediction.
            xy = _DIAG_FAKE_XY.copy()
            c = _DIAG_FAKE_C.copy()
        return xy, c
