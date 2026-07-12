"""HDF5 session store — only write path for schema (§5.3 + esp_us/noise extension) (spec decision ②).

/links contains raw values only: correction/interpolation/resampling are build artifacts
(/grid, /samples — samples.py) and can be re-run at any time. (I,Q) order in iq pairs must be
re-verified when §16-6 SC table is finalized — no impact on amplitude calculation.
"""
import json
import threading
import time

import h5py
import numpy as np

_LINK_FIELDS = [
    ("t_ns", np.uint64, ()),
    ("esp_us", np.uint64, ()),
    ("iq", np.int8, (56, 2)),
    ("seq", np.uint32, ()),
    ("rssi", np.int8, ()),
    ("noise", np.int8, ()),
    ("boot_id", np.uint8, ()),
]


class SessionWriter:
    def __init__(self, path, *, meta=None, chunk=4096):
        # h5py is not thread-safe — recorder uses paho callback thread (append) and main thread
        # (periodic flush) concurrently, so all public mutation methods are serialized with a lock.
        # RLock because: close->flush, etc. internal re-calls (re-entry) occur while holding the lock.
        self._lock = threading.RLock()
        self._h = h5py.File(path, "w")
        self._meta = self._h.create_group("meta")
        self.set_meta("created_ns", time.time_ns())
        for k, v in (meta or {}).items():
            self.set_meta(k, v)
        self._chunk = chunk
        self._closed = False
        self._buf = {}      # (rx,tx) -> {field: list}
        self._counts = {}   # (rx,tx) -> record count
        self._vid = []      # (t_ns, frame_idx) pairs
        self._h.create_dataset("video/t_ns", shape=(0,), maxshape=(None,),
                               dtype=np.uint64, chunks=(1024,))
        self._h.create_dataset("video/frame_idx", shape=(0,), maxshape=(None,),
                               dtype=np.uint32, chunks=(1024,))
        # dev_doc/17 §3.2: per-video-frame segment 标记
        self._h.create_dataset("video/segment_idx", shape=(0,), maxshape=(None,),
                               dtype=np.uint32, chunks=(1024,))
        self._h.create_dataset("video/state", shape=(0,), maxshape=(None,),
                               dtype=np.uint8, chunks=(1024,))
        self._segments_meta = []   # dev_doc/17 §3.4

    def set_meta(self, k, v):
        with self._lock:
            self._meta.attrs[k] = (v if isinstance(v, (int, float, str, np.integer))
                                   else json.dumps(v, ensure_ascii=False))

    def append(self, *, rx_id, tx_idx, t_ns, esp_us, iq, seq, rssi, noise, boot_id):
        with self._lock:
            key = (int(rx_id), int(tx_idx))
            buf = self._buf.get(key)
            if buf is None:
                buf = self._buf[key] = {name: [] for name, _, _ in _LINK_FIELDS}
                self._ensure_link(key)
            iq_arr = np.frombuffer(iq, dtype=np.int8).reshape(56, 2) if isinstance(
                iq, (bytes, bytearray)) else np.asarray(iq, np.int8).reshape(56, 2)
            vals = {"t_ns": t_ns, "esp_us": esp_us, "iq": iq_arr, "seq": seq,
                    "rssi": rssi, "noise": noise, "boot_id": boot_id}
            for name in buf:
                buf[name].append(vals[name])
            if len(buf["t_ns"]) >= self._chunk:
                self._flush_link(key)

    def append_video(self, t_ns, frame_idx, *, seg_idx=0, state=1):
        """dev_doc/17 §3.4: seg_idx/state 是关键字参数，默认 action (backward compat)。"""
        with self._lock:
            self._vid.append((int(t_ns), int(frame_idx), int(seg_idx), int(state)))
            if len(self._vid) >= 1024:
                self._flush_video()

    def update_segment(self, *, start_t_ns, end_t_ns, name, state):
        """dev_doc/17 §3.4: 累积段范围表，close() 时写入 meta/segments JSON。"""
        with self._lock:
            self._segments_meta.append({
                "start_t_ns": int(start_t_ns),
                "end_t_ns": int(end_t_ns),
                "name": str(name),
                "state": str(state),
            })

    def _ensure_link(self, key):
        g = self._h.require_group(f"links/{key[0]}{key[1]}")
        for name, dt, extra in _LINK_FIELDS:
            g.create_dataset(name, shape=(0, *extra), maxshape=(None, *extra),
                             dtype=dt, chunks=(self._chunk, *extra))
        self._counts[key] = 0

    def _flush_link(self, key):
        buf = self._buf[key]
        m = len(buf["t_ns"])
        if not m:
            return
        g = self._h[f"links/{key[0]}{key[1]}"]
        n = self._counts[key]
        for name, dt, _ in _LINK_FIELDS:
            ds = g[name]
            ds.resize(n + m, axis=0)
            ds[n:] = np.asarray(buf[name], dtype=dt)
            buf[name].clear()
        self._counts[key] = n + m

    def _flush_video(self):
        if not self._vid:
            return
        ts = self._h["video/t_ns"]
        fi = self._h["video/frame_idx"]
        si = self._h["video/segment_idx"]
        st = self._h["video/state"]
        n = ts.shape[0]
        m = len(self._vid)
        ts.resize(n + m, axis=0)
        fi.resize(n + m, axis=0)
        si.resize(n + m, axis=0)
        st.resize(n + m, axis=0)
        ts[n:] = np.asarray([v[0] for v in self._vid], np.uint64)
        fi[n:] = np.asarray([v[1] for v in self._vid], np.uint32)
        si[n:] = np.asarray([v[2] for v in self._vid], np.uint32)
        st[n:] = np.asarray([v[3] for v in self._vid], np.uint8)
        self._vid.clear()

    def flush(self):
        with self._lock:
            for key in list(self._buf):
                self._flush_link(key)
            self._flush_video()
            self._h.flush()

    def close(self):
        with self._lock:
            if self._closed:    # Idempotent — double close is harmless (append-after-close still raises)
                return
            self._closed = True
            self.flush()
            self.set_meta("frames_total", int(sum(self._counts.values())))
            self.set_meta("links", json.dumps(
                {f"{k[0]}{k[1]}": int(v) for k, v in sorted(self._counts.items())}))
            # dev_doc/17 §3.4: 写入 meta/segments 范围表
            self.set_meta("segments", json.dumps(self._segments_meta, ensure_ascii=False))
            self._h.close()


class SessionReader:
    def __init__(self, path):
        self._h = h5py.File(path, "r")

    @property
    def meta(self):
        return dict(self._h["meta"].attrs)

    def link_keys(self):
        if "links" not in self._h:
            return []
        return [(int(n[0]), int(n[1])) for n in sorted(self._h["links"])]

    def link(self, i, j):
        g = self._h[f"links/{i}{j}"]
        return {name: g[name][...] for name in g}

    @property
    def video_t_ns(self):
        return self._h["video/t_ns"][...]

    @property
    def video_frame_idx(self):
        """Legacy sessions (no field) return None — caller falls back to identity."""
        if "video/frame_idx" not in self._h:
            return None
        return self._h["video/frame_idx"][...]

    def close(self):
        self._h.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
