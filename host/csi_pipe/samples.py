"""§5.2 build orchestration — clockfit -> link stream -> /grid -> /samples.

Block-by-block recording for long-session memory limits. Anchor = /video/t_ns (if present)
or synthetic equal-interval (anchor_rate Hz)."""
import json
from pathlib import Path

import h5py
import numpy as np

from .align import (LinkStream, amplitude, cut_windows, fill_gaps, grid_block,
                    grid_bounds, sanitized_phase, split_epochs, window_indices, WIN)
from .clockfit import fit_board

STEP_NS = 10_000_000  # 100Hz
N_FEAT = 113                          # value columns: amp[:56] || phase[56:112] || rssi[112]


def anchor_shift_ns(csi_ms, cam_ms):
    """§5.2-3 correction anchor shift: anchor' = vid - shift, shift = (cam - csi) ms -> int ns.

    Derived from convention 'true_value = stamp - correction' — aligns window end to grid position
    (T + csi_corr) of scene event T (spec 2026-06-11-correction-apply). Non-finite or |shift|>1s fail-loud."""

    if not (np.isfinite(csi_ms) and np.isfinite(cam_ms)):
        raise SystemExit(f"Correction value non-finite — csi={csi_ms} cam={cam_ms}")
    shift_ms = float(cam_ms) - float(csi_ms)
    if abs(shift_ms) > 1000.0:
        raise SystemExit(f"Correction shift |{shift_ms:.1f}ms| > 1000ms — suspect unit (ms) error")
    return int(round(shift_ms * 1e6))


def resolve_corrections(csi_ms=None, cam_ms=None, no_correction=False,
                        config_path=None):
    """CLI correction value parsing — priority: no_correction > individual flags > config file.

    Returns (corrections | None, source). corrections = {"csi_ms", "cam_ms"}.
    - config absent -> (None, "absent") pass through (synthetic sessions allowed — video sessions build fail-loud)
    - config exists but parsing fails/missing required keys -> SystemExit (do not silently treat as absent)
    - after merge, if only one side has a value -> SystemExit
    - if any flag is provided, source = "cli" (values are still recorded in meta for traceability)"""
    if no_correction:
        return {"csi_ms": 0.0, "cam_ms": 0.0}, "off"
    cfg_csi = cfg_cam = None
    if config_path is not None and Path(config_path).exists():
        try:
            cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
            cfg_csi = float(cfg["csi_correction_ms"])
            cfg_cam = float(cfg["cam_correction_ms"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            raise SystemExit(f"Pairing config corrupted ({config_path}): {e}")
    c = csi_ms if csi_ms is not None else cfg_csi
    m = cam_ms if cam_ms is not None else cfg_cam
    if c is None and m is None:
        return None, "absent"
    if c is None or m is None:
        raise SystemExit("Both correction values are needed — only one of csi/cam determined "
                         f"(csi={c}, cam={m})")
    src = "cli" if (csi_ms is not None or cam_ms is not None) else "config"
    return {"csi_ms": float(c), "cam_ms": float(m)}, src


def _link_stream(d, model, *, max_gap_run=2, label="?"):
    """Link raw arrays + board clock model -> LinkStream (epoch merge, inter-epoch = break).

    Value columns [n,113] = amplitude||sanitized phase||rssi — column concat for interpolation/grid 1-pass
    (M2.5 §1.2).
    Sanitization is applied to raw packets before interpolation (linear component varies per packet, so
    interpolation is only meaningful after...)."""
    tf, ok = model.predict(d["esp_us"].astype(np.float64), d["boot_id"])
    vals = np.concatenate([amplitude(d["iq"]), sanitized_phase(d["iq"]),
                           np.asarray(d["rssi"], np.float32)[:, None]], axis=1)
    parts_t, parts_a, parts_m, breaks = [], [], [], []
    for sl in split_epochs(d["seq"], d["boot_id"]):
        keep = ok[sl]
        t = tf[sl][keep].astype(np.int64)
        a = vals[sl][keep]
        sq = np.asarray(d["seq"], np.int64)[sl][keep]
        if len(t) < 2:
            continue
        t2, a2, m2, br = fill_gaps(t, sq, a, max_run=max_gap_run)
        if parts_t:
            breaks.append((int(parts_t[-1][-1]), int(t2[0])))
        parts_t.append(t2)
        parts_a.append(a2)
        parts_m.append(m2)
        breaks.extend(br)
    if not parts_t:
        return None
    t_all = np.concatenate(parts_t)
    if (np.diff(t_all) < 0).any():      # Backward = garbage interpolation — fail silently not allowed
        raise SystemExit(f"Link {label}: merged stream t goes backward — check epoch order/clockfit")
    return LinkStream(t=t_all, amp=np.concatenate(parts_a),
                      interp=np.concatenate(parts_m), breaks=sorted(breaks))


def _fit_rx(ds, window_s):
    """rx link fit fields (t_ns·esp_us·boot_id) list -> (model, stats). Local arrays freed on return."""
    t = np.concatenate([d["t_ns"] for d in ds]).astype(np.int64)
    esp = np.concatenate([d["esp_us"] for d in ds]).astype(np.float64)
    boot = np.concatenate([d["boot_id"] for d in ds])
    o = np.argsort(t, kind="stable")               # Arrival order (preserve epoch order)
    model, rep = fit_board(esp[o], t[o], boot[o], window_s=window_s)
    return model, rep.stats()


def _cut_into(out_ds, grid_ds, mask_ds, starts, *, label, say):
    """Grid -> window cutting, recorded block-by-block into out_ds + finite fail-loud. Returns valid.

    Sequential pass per feature (memory peak = one slice at a time). If feature is like rssi [rows,3,3],
    K=1 axis is assigned.
    cut_windows is K=56 specific, so for K!=56 slice directly (mask decision reused)."""
    valid_all = np.zeros(len(starts), bool)
    span = 100_000                                  # Grid row unit batch
    n_done = 0
    while n_done < len(starts):
        lo_row = int(starts[n_done])
        hi_idx = int(np.searchsorted(starts, lo_row + span - WIN, side="right"))
        hi_idx = max(hi_idx, n_done + 1)
        rows0 = lo_row
        rows1 = int(starts[hi_idx - 1]) + WIN
        mem = np.asarray(grid_ds[rows0:rows1], np.float32)
        mask_mem = mask_ds[rows0:rows1][...]
        rel = starts[n_done:hi_idx] - rows0
        if mem.ndim == 3:
            # rssi: [rows,3,3] -> slices [N,WIN,3,3] without cut_windows reshape assumption
            idx = rel[:, None] + np.arange(WIN)[None, :]        # [N,WIN]
            X = mem[idx].astype(np.float16)                     # [N,WIN,3,3]
            bad = mask_mem[idx].sum(axis=1)                     # [N,3,3]
            valid = (bad < 2).all(axis=(1, 2))
        else:
            # amp/phase: [rows,K,3,3] -> cut_windows -> [N,WIN*K,3,3]
            X, valid = cut_windows(mem, mask_mem, rel)
        if not np.isfinite(X).all():
            raise SystemExit(f"{label}: Non-finite value — stop build (check raw/clockfit)")
        out_ds[n_done:hi_idx] = X
        valid_all[n_done:hi_idx] = valid
        n_done = hi_idx
        say(f"{label} {n_done}/{len(starts)}")
    return valid_all


def build(h5_path, *, anchor_rate=20.0, fit_window_s=600.0, force=False,
          block=100_000, max_gap_run=2, corrections=None, progress=None):
    """Add /grid·/samples to session HDF5. Returns {"G","N","fit"}.

    corrections: {"csi_ms", "cam_ms"[, "source"]} | None — §5.2-3 correction anchor
    (spec 2026-06-11-correction-apply). Video-anchor sessions required (fail-loud),
    synthetic anchor ignored. /samples/t_ns always uses raw video timestamps (pam join contract)."""
    say = progress or (lambda s: None)
    h = h5py.File(h5_path, "r+")
    try:
        if ("grid" in h or "samples" in h):
            if not force:
                raise SystemExit("Existing /grid·/samples — use --force to rebuild")
            for k in ("grid", "samples"):
                if k in h:
                    del h[k]
        names = sorted(h["links"])
        # Pre-load only clockfit fields — large raw (iq) loaded per-link (reduce memory peak)
        per_rx = {}
        for name in names:
            g = h[f"links/{name}"]
            per_rx.setdefault(int(name[0]), []).append(
                {k: g[k][...] for k in ("t_ns", "esp_us", "boot_id")})
        # Per-board (rx) clockfit — 3 links on same board share same clock
        models, fit_stats = {}, {}
        for i in sorted(per_rx):
            models[i], fit_stats[i] = _fit_rx(per_rx.pop(i), fit_window_s)
            say(f"rx{i} clockfit: {fit_stats[i]}")
        # Link streams — per-link load -> amplitude transform -> release raw before next link load
        streams = {}
        for name in names:
            key = (int(name[0]), int(name[1]))
            g = h[f"links/{name}"]
            d = {k: g[k][...] for k in ("esp_us", "boot_id", "iq", "seq", "rssi")}
            s = _link_stream(d, models[key[0]], max_gap_run=max_gap_run, label=key)
            del d
            if s is None:
                raise SystemExit(f"Link {key}: Insufficient valid samples — cannot build")
            streams[key] = s
        g0, g1 = grid_bounds(list(streams.values()), step_ns=STEP_NS)
        G = int((g1 - g0) // STEP_NS)
        if G < WIN:
            raise SystemExit("Common available interval shorter than window")
        gg = h.create_group("grid")
        gg.create_dataset("t_ns", data=(g0 + STEP_NS * np.arange(G, dtype=np.int64))
                          .astype(np.uint64))
        amp_ds = gg.create_dataset("amp", shape=(G, 56, 3, 3), dtype=np.float16,
                                   chunks=(min(4096, G), 56, 3, 3))
        phase_ds = gg.create_dataset("phase", shape=(G, 56, 3, 3), dtype=np.float16,
                                     chunks=(min(4096, G), 56, 3, 3))
        rssi_ds = gg.create_dataset("rssi", shape=(G, 3, 3), dtype=np.float16,
                                    chunks=(min(65536, G), 3, 3))
        mask_ds = gg.create_dataset("mask", shape=(G, 3, 3), dtype=bool,
                                    chunks=(min(65536, G), 3, 3))
        for b0 in range(0, G, block):
            b1 = min(b0 + block, G)
            tb = g0 + STEP_NS * np.arange(b0, b1, dtype=np.int64)
            feat_blk = np.zeros((b1 - b0, N_FEAT, 3, 3), np.float32)
            mask_blk = np.zeros((b1 - b0, 3, 3), bool)
            for (i, j), s in streams.items():
                a, m = grid_block(s, tb)
                feat_blk[:, :, i, j] = a
                mask_blk[:, i, j] = m
            amp_ds[b0:b1] = feat_blk[:, :56].astype(np.float16)
            phase_ds[b0:b1] = feat_blk[:, 56:112].astype(np.float16)
            rssi_ds[b0:b1] = feat_blk[:, 112].astype(np.float16)
            mask_ds[b0:b1] = mask_blk
            say(f"Grid {b1}/{G}")
        # Anchor (+§5.2-3 correction shift — only window selection is corrected, stored t_ns remains raw)
        vid = h["video/t_ns"][...].astype(np.int64)
        if len(vid):
            if corrections is None:
                raise SystemExit(
                    "Video-anchor session — correction values required: configs/pairing.json or "
                    "--csi-corr-ms/--cam-corr-ms (use --no-correction for no-correction build)")
            shift_ns = anchor_shift_ns(corrections["csi_ms"], corrections["cam_ms"])
            corr_meta = {"csi_ms": float(corrections["csi_ms"]),
                         "cam_ms": float(corrections["cam_ms"]),
                         "shift_ms": shift_ns / 1e6,
                         "source": corrections.get("source", "cli")}
            anchors = vid
            src = "video"
        else:
            shift_ns = 0
            corr_meta = {"source": "n/a(synthetic)"}
            anchors = np.arange(g0 + WIN * STEP_NS, g1 + 1,
                                round(1e9 / anchor_rate), dtype=np.int64)
            src = f"synthetic@{anchor_rate}Hz"
        anchors = np.sort(anchors)      # Video anchor order not guaranteed — batching searchsorted prerequisite
        starts, ok = window_indices(g0, STEP_NS, G, anchors - shift_ns)
        starts, anchors = starts[ok], anchors[ok]
        if len(starts) == 0:
            raise SystemExit("No valid anchor — anchor time does not overlap with grid")
        sg = h.create_group("samples")
        N = len(starts)
        X_ds = sg.create_dataset("X", shape=(N, WIN * 56, 3, 3), dtype=np.float16,
                                 chunks=(min(1024, max(1, N)), WIN * 56, 3, 3))
        XP_ds = sg.create_dataset("X_phase", shape=(N, WIN * 56, 3, 3), dtype=np.float16,
                                  chunks=(min(1024, max(1, N)), WIN * 56, 3, 3))
        RS_ds = sg.create_dataset("rssi", shape=(N, WIN, 3, 3), dtype=np.float16,
                                  chunks=(min(4096, max(1, N)), WIN, 3, 3))
        valid_all = _cut_into(X_ds, amp_ds, mask_ds, starts, label="Sample", say=say)
        v_p = _cut_into(XP_ds, phase_ds, mask_ds, starts, label="Sample(phase)", say=say)
        v_r = _cut_into(RS_ds, rssi_ds, mask_ds, starts, label="Sample(rssi)", say=say)
        if not (np.array_equal(valid_all, v_p) and np.array_equal(valid_all, v_r)):
            raise SystemExit("X/X_phase/rssi valid mismatch — internal build error")
        sg.create_dataset("t_ns", data=anchors.astype(np.uint64))
        sg.create_dataset("valid", data=valid_all)
        h["meta"].attrs["build"] = json.dumps(
            {"anchors": src, "G": G, "N": len(starts),
             "features": ["amp", "phase", "rssi"],
             "corrections": corr_meta,
             "fit": {str(k): v for k, v in fit_stats.items()},
             "fit_window_s": fit_window_s}, ensure_ascii=False)
        return {"G": G, "N": int(len(starts)), "fit": fit_stats,
                "valid_ratio": float(valid_all.mean()) if len(valid_all) else 0.0}
    finally:
        h.close()
