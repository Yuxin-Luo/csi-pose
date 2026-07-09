#!/usr/bin/env python3
"""Build aligned samples in session HDF5 (/grid + /samples/X).

  python3 build_samples.py --h5 ../sessions/soak-20260610.h5 --anchor-rate 20

Video anchor sessions require correction values — loaded from
<repo>/configs/pairing.json by default, override with --csi-corr-ms/--cam-corr-ms,
ablation uses --no-correction (explicit 0·0). Applied values are recorded in /meta attrs build.corrections.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from csi_pipe.samples import build, resolve_corrections  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--h5", required=True)
    ap.add_argument("--anchor-rate", type=float, default=20.0,
                    help="Synthetic anchor Hz when no video (default 20)")
    ap.add_argument("--fit-window", type=float, default=600.0)
    ap.add_argument("--force", action="store_true", help="Rebuild existing /grid and /samples")
    ap.add_argument("--pairing-config", default=None,
                    help="Correction values JSON (default: <repo>/configs/pairing.json)")
    ap.add_argument("--csi-corr-ms", type=float, default=None,
                    help="CSI system correction in ms — override config")
    ap.add_argument("--cam-corr-ms", type=float, default=None,
                    help="Camera system correction in ms — override config")
    ap.add_argument("--no-correction", action="store_true",
                    help="Explicit no-correction (0·0) — for ablation/debug")
    args = ap.parse_args()
    if args.pairing_config is not None and not args.pairing_config.strip():
        ap.error("--pairing-config is an empty path — default config uses the flag omission, "
                 "no-correction uses --no-correction (suspect unset shell variable)")
    if args.pairing_config and not Path(args.pairing_config).exists():
        ap.error(f"--pairing-config file not found: {args.pairing_config}")
    cfg = (Path(args.pairing_config) if args.pairing_config
           else Path(__file__).resolve().parents[2] / "configs" / "pairing.json")
    corr, src = resolve_corrections(csi_ms=args.csi_corr_ms, cam_ms=args.cam_corr_ms,
                                    no_correction=args.no_correction, config_path=cfg)
    if corr is not None:
        corr = {**corr, "source": src}
        print(f"Correction: csi={corr['csi_ms']}ms cam={corr['cam_ms']}ms ({src})")
    info = build(Path(args.h5), anchor_rate=args.anchor_rate,
                 fit_window_s=args.fit_window, force=args.force,
                 corrections=corr, progress=lambda s: print(s, flush=True))
    print(f"Done: G={info['G']} N={info['N']} valid={info['valid_ratio']:.1%}")


if __name__ == "__main__":
    main()
