"""rt.yaml load+validation — missing keys/file absent = SystemExit (fail-loud, no silent fallback)."""
from pathlib import Path

import yaml

_TOP = ("tau_presence", "ema_alpha", "settle_ms", "motion_window_s", "fall", "mqtt")
_FALL = ("theta_v", "aspect_hi", "aspect_lo", "transition_s", "grace_s", "head_y", "confirm_s",
         "lying_ratio", "theta_still", "theta_csi_still", "refractory_s",
         "release_hip_y", "release_hold_s", "absent_release_s")
_MQTT = ("host", "port")


def load_rt_config(path):
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"rt config not found: {p} — specify --config with configs/rt.yaml path")
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise SystemExit(f"rt config format error (top-level mapping absent or empty): {p}")
    for sect, keys in (("", _TOP), ("fall", _FALL), ("mqtt", _MQTT)):
        d = cfg if not sect else cfg.get(sect) or {}
        for k in keys:
            if k not in d:
                raise SystemExit(f"rt config key missing: {sect + '.' if sect else ''}{k} ({p})")
    return cfg
