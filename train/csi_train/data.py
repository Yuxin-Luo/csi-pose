"""Session HDF5 loading, normalization, in-memory splitting.

Row selection = valid & label_ok. presence is a loss mask, not row exclusion.
Normalization order: packet·link L2 -> link z-score (train stats only) -> f16 storage.
"""
from dataclasses import dataclass
from pathlib import Path

import h5py
import numpy as np
import yaml

CHANNEL_CONVENTION = "X[c,i,j]: c=p*56+k (p 0..4 past->present, k SC low->high), [i,j]=(rx_i,tx_j)"
IN_CH, N_JOINTS = 280, 18
HYPER_DEFAULTS = {"batch": 64, "epochs": 30, "warmup": 2, "lr": 1e-3, "wd": 1e-4,
                  "knn_k": 5, "seed": 0}
FEATURES = ("phase", "rssi")          # Supported features — stored as aligned list in config·ckpt


@dataclass
class Rows:
    X: np.ndarray         # (N,280,3,3) f16 — raw after load, normalized after build_splits (560 when combining features)
    Y: np.ndarray         # (N,4,18,18) f32 — PAM (normalized coordinates)
    presence: np.ndarray  # (N,) bool
    WH: np.ndarray        # (N,2) f32 — for pixel conversion
    stype: np.ndarray     # (N,) <U32 — report breakdown tag
    XP: np.ndarray = None  # (N,280,3,3) f16 | None — sanitized phase (M2.5)
    RS: np.ndarray = None  # (N,5,3,3) f16 | None — 5-packet window RSSI dB (M2.5)


def load_manifest(path):
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"Config file not found: {p}")
    man = yaml.safe_load(p.read_text(encoding="utf-8"))
    man["hyper"] = merge_hyper(man.get("hyper") or {})
    return man


def merge_hyper(hyper):
    return {**HYPER_DEFAULTS, **hyper}


def _canon_features(features):
    feats = tuple(sorted(set(features)))
    unknown = set(feats) - set(FEATURES)
    if unknown:
        raise SystemExit(f"Undefined features {sorted(unknown)} — supported: {sorted(FEATURES)}")
    return feats


def load_session(h5_path, features=()):
    """Single session -> filtered raw dict {X,Y,presence,W,H[,XP,RS]}."""
    feats = _canon_features(features)
    with h5py.File(h5_path, "r") as h:
        if h["samples/X"].shape[1:] != (IN_CH, 3, 3):
            raise SystemExit(f"{h5_path}: X shape {h['samples/X'].shape[1:]} != (280,3,3) — Section 6.1 violation")
        keep = h["samples/valid"][...] & h["samples/label_ok"][...]
        out = {"X": h["samples/X"][...][keep].astype(np.float16),
               "Y": h["samples/Y"][...][keep].astype(np.float32),
               "presence": h["samples/presence"][...][keep],
               "W": int(h["labels"].attrs["W"]), "H": int(h["labels"].attrs["H"])}
        spec = {"phase": ("samples/X_phase", (IN_CH, 3, 3), "XP"),
                "rssi": ("samples/rssi", (5, 3, 3), "RS")}
        for f in feats:
            ds, shp, key = spec[f]
            if ds not in h:
                raise SystemExit(f"{h5_path}: {ds} not found — old build, need M2.5 rebuild with build_samples.py --force (Section 2.1)")
            arr = h[ds][...]
            if arr.shape[1:] != shp:
                raise SystemExit(f"{h5_path}: {ds} shape {arr.shape[1:]} != {shp}")
            out[key] = arr[keep].astype(np.float16)
        return out


def load_role(manifest, role, features=()):
    """Manifest's role sessions -> combined Rows (X·XP·RS are raw f16)."""
    feats = _canon_features(features)
    entries = [s for s in manifest["sessions"] if s["role"] == role]
    if not entries:
        raise SystemExit(f"No role={role} sessions in manifest — check configs/train.yaml")
    parts = []
    for e in entries:
        s = load_session(e["h5"], feats)
        n = len(s["X"])
        parts.append((s, np.full((n, 2), [s["W"], s["H"]], np.float32),
                      np.full(n, str(e.get("type", "")), dtype="<U32")))
    rows = Rows(X=np.concatenate([p[0]["X"] for p in parts]),
                Y=np.concatenate([p[0]["Y"] for p in parts]),
                presence=np.concatenate([p[0]["presence"] for p in parts]),
                WH=np.concatenate([p[1] for p in parts]),
                stype=np.concatenate([p[2] for p in parts]))
    if "phase" in feats:
        rows.XP = np.concatenate([p[0]["XP"] for p in parts])
    if "rssi" in feats:
        rows.RS = np.concatenate([p[0]["RS"] for p in parts])
    return rows


def l2_normalize(X):
    """Per-packet·link 56-dim L2 (Section 6.2). 0-vectors (filled for missing) remain 0. Returns f32."""
    X5 = X.astype(np.float32).reshape(len(X), 5, 56, 3, 3)
    n = np.linalg.norm(X5, axis=2, keepdims=True)
    return (X5 / np.where(n == 0, 1, n)).reshape(len(X), IN_CH, 3, 3)


def fit_stats(Xn):
    """Link-wise z-score stats after L2 — (mu(3,3), sigma(3,3)) f32."""
    mu = Xn.mean(axis=(0, 1))
    sigma = Xn.std(axis=(0, 1))
    sigma[sigma == 0] = 1.0
    return mu.astype(np.float32), sigma.astype(np.float32)


def apply_stats(Xn, mu, sigma):
    """z-score then f16. Caution: Xn (f32) is mutated in-place — dedicated to l2_normalize output."""
    Xn -= mu
    Xn /= sigma
    return Xn.astype(np.float16)


def rssi_rescale(Xn, RS):
    """Section 6.2 correct method — after L2, multiply by 10^(RSSI/20) (per packet·link). Xn (f32) mutated in place."""
    X5 = Xn.reshape(len(Xn), 5, 56, 3, 3)
    X5 *= 10.0 ** (np.asarray(RS, np.float32)[:, :, None] / 20.0)
    return Xn


def normalize_rows(rows, features, mu, sigma, mu_phase=None, sigma_phase=None):
    """Rows -> combined X (f16) — amp: L2 (->rssi rescale) ->z-score / phase: its own z-score."""
    feats = _canon_features(features)
    Xn = l2_normalize(rows.X)
    if "rssi" in feats:
        Xn = rssi_rescale(Xn, rows.RS)
    X = apply_stats(Xn, mu, sigma)
    if "phase" in feats:
        X = np.concatenate([X, apply_stats(rows.XP.astype(np.float32),
                                           mu_phase, sigma_phase)], axis=1)
    return X


def build_splits(manifest, features=()):
    """{"train","val","mu","sigma","mu_phase","sigma_phase","features"} — X normalized (f16).

    One f32 intermediate copy per split only (suppresses real-data peaks — quality review feedback).
    Train inline path and normalize_rows use the same formula — change both if normalization order changes."""
    feats = _canon_features(features)
    tr, va = load_role(manifest, "train", feats), load_role(manifest, "val", feats)
    Xtr = l2_normalize(tr.X)
    if "rssi" in feats:
        Xtr = rssi_rescale(Xtr, tr.RS)
    mu, sigma = fit_stats(Xtr)
    mu_p = sigma_p = None
    X16 = apply_stats(Xtr, mu, sigma)
    del Xtr
    if "phase" in feats:
        Ptr = tr.XP.astype(np.float32)
        mu_p, sigma_p = fit_stats(Ptr)
        X16 = np.concatenate([X16, apply_stats(Ptr, mu_p, sigma_p)], axis=1)
        del Ptr
    tr.X = X16
    va.X = normalize_rows(va, feats, mu, sigma, mu_p, sigma_p)
    return {"train": tr, "val": va, "mu": mu, "sigma": sigma,
            "mu_phase": mu_p, "sigma_phase": sigma_p, "features": list(feats)}


def diag_pose(Y):
    """Y (N,4,18,18) -> (xy (N,18,2) f32, c (N,18) f32) — PAM diagonal."""
    d = np.arange(Y.shape[-1])
    xy = np.stack([Y[:, 0, d, d], Y[:, 1, d, d]], axis=2)
    return xy.astype(np.float32), Y[:, 2, d, d].astype(np.float32)
