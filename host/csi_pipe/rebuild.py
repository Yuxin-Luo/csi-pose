"""Rebuild session HDF5 from rawlog (bridge original).

unwrap is continuous across file boundaries per rx board (if boot_id is the same, it's the same epoch
even if bridge restart splits the log). StreamParser is per file (re-sync boundary)."""
from csi_host.framing import StreamParser
from csi_host.rawlog import read_rawlog
from csi_host.unwrap import TimeUnwrapper

from .store import SessionWriter


def rebuild_session(rx_paths, out_path, *, session, extra_meta=None, progress=None):
    """rx_paths: {rx_id: [rawlog paths (chronological)]} -> out_path HDF5. Returns {rx: {frames, crc, mismatch}}."""
    meta = {"session": session,
            "sources": {str(rx): [str(p) for p in ps] for rx, ps in rx_paths.items()}}
    meta.update(extra_meta or {})
    w = SessionWriter(out_path, meta=meta)
    stats = {}
    try:
        for rx_id, paths in sorted(rx_paths.items()):
            unwrap = TimeUnwrapper()
            frames = crc = mismatch = 0
            for p in paths:
                parser = StreamParser()
                for t_ns, chunk in read_rawlog(p):
                    for kind, val in parser.feed(chunk):
                        if kind != "frame":
                            continue
                        if val.rx_id != rx_id:
                            mismatch += 1
                            continue
                        u, _ = unwrap.update(boot_id=val.boot_id,
                                             t_us=val.esp_timer_us)
                        w.append(rx_id=val.rx_id, tx_idx=val.tx_idx, t_ns=t_ns,
                                 esp_us=u, iq=val.iq, seq=val.seq, rssi=val.rssi,
                                 noise=val.noise_floor, boot_id=val.boot_id)
                        frames += 1
                crc += parser.crc_errors
                if progress:
                    progress(f"rx{rx_id} {p}: cumulative {frames} frames")
            stats[rx_id] = {"frames": frames, "crc": crc, "mismatch": mismatch}
            w.set_meta(f"rx{rx_id}_crc_errors", crc)
    except BaseException:
        try:
            w.close()       # Don't obscure the real exception with close failure in corrupted state
        except Exception:
            pass
        raise
    else:
        w.close()           # Normal path: close errors are surfaced
    return stats
