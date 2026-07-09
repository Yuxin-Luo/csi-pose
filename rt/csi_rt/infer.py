"""Checkpoint inference wrapper — reuse load_ckpt·l2_normalize, extract diagonal (x,y,c).

Normalization = l2(packet·link) -> (X-mu)/sigma (mu·sigma from ckpt, do not recalculate).
--random-weights: M2 pre-pipe smoke test only (seed 0 fixed — test determinism)."""
import time
from pathlib import Path

import numpy as np
import torch

from csi_train.data import CHANNEL_CONVENTION, IN_CH, l2_normalize
from csi_train.fit import load_ckpt
from csi_train.model import WiSPPN


class PoseEstimator:
    def __init__(self, ckpt_path, *, random_weights=False, device=None):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.random = bool(random_weights)
        self.infer_ms = 0.0
        if self.random:
            if ckpt_path:
                raise SystemExit("--ckpt and --random-weights cannot be specified together")
            torch.manual_seed(0)
            self.model = WiSPPN(in_ch=IN_CH).to(self.device).eval()
            self.vector_head = False
            self.mu = np.zeros((3, 3), np.float32)     # Same shape as real ckpt fit_stats — (…,3,3) broadcast
            self.sigma = np.ones((3, 3), np.float32)
            return
        if not ckpt_path or not Path(ckpt_path).exists():
            raise SystemExit(f"ckpt not found: {ckpt_path} — specify --ckpt or --random-weights "
                             "(pipe smoke test only)")
        self.model, ck = load_ckpt(ckpt_path, device=self.device)
        if ck.get("channel_convention") != CHANNEL_CONVENTION:
            raise SystemExit(f"ckpt channel convention mismatch: {ck.get('channel_convention')!r} "
                             f"!= {CHANNEL_CONVENTION!r}")
        self.vector_head = bool(ck["config"]["vector_head"])
        self.mu = ck["mu"].cpu().numpy().astype(np.float32)
        self.sigma = ck["sigma"].cpu().numpy().astype(np.float32)

    def __call__(self, X_raw):
        """X_raw (280,3,3) f32/f16 -> (xy (18,2) f32 normalized coordinates, c (18,) f32)."""
        Xn = l2_normalize(np.asarray(X_raw, np.float32)[None])
        Xn = (Xn - self.mu) / self.sigma
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
        return v[:2].T.astype(np.float32).copy(), v[2].astype(np.float32).copy()
