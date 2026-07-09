# """Append-only original log (bridge raw log is the original, HDF5 rebuild possible).
#
# File = header 8B b"CSIRAW01" + repeating records. Record = t_ns u64 LE + len u16 LE + bytes.
# Incomplete tail (interrupted by crash) is ignored on read. Periodic flush preserves partial data on crash.
# """
"""Append-only original log (bridge raw log is the original, HDF5 rebuild possible).

File = header 8B b"CSIRAW01" + repeating records. Record = t_ns u64 LE + len u16 LE + bytes.
Incomplete tail (interrupted by crash) is ignored on read. Periodic flush preserves partial data on crash.
"""
import struct

HEADER = b"CSIRAW01"
_REC = struct.Struct("<QH")


class RawLogWriter:
    def __init__(self, path, flush_every: int = 64):
        self._f = open(path, "ab")
        self._flush_every = flush_every
        self._since_flush = 0
        if self._f.tell() == 0:
            self._f.write(HEADER)
            self._f.flush()

    def append(self, t_ns: int, data: bytes):
        self._f.write(_REC.pack(t_ns, len(data)))
        self._f.write(data)
        self._since_flush += 1
        if self._since_flush >= self._flush_every:
            self._f.flush()
            self._since_flush = 0

    def close(self):
        self._f.flush()
        self._f.close()


def read_rawlog(path):
    """(t_ns, bytes) generator -- silently terminates on incomplete tail."""
    with open(path, "rb") as f:
        if f.read(len(HEADER)) != HEADER:
            raise ValueError(f"not a rawlog (bad header): {path}")
        while True:
            hdr = f.read(_REC.size)
            if len(hdr) < _REC.size:
                return
            t_ns, n = _REC.unpack(hdr)
            data = f.read(n)
            if len(data) < n:
                return
            yield t_ns, data
