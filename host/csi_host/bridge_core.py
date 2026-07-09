# Bridge core — pure logic injected with serial I/O and MQTT.
#
# ingest(t_ns, chunk): raw log (original) -> stream parse -> link gap/unwrap aggregation -> sink publish.
# t_host (time.time_ns) right after serial read is the timestamp origin (Section 5 principle) — caller stamps it.

import struct

from .framing import StreamParser
from .gap import LinkTracker
from .rawlog import RawLogWriter
from .unwrap import TimeUnwrapper

try:
    import msgpack
except ImportError:  # Core requires stdlib only — msgpack is optional
    msgpack = None

_FALLBACK = struct.Struct("<4sQ")
_FALLBACK_MAGIC = b"CSIB"


def pack_csi(t_ns: int, frame: bytes) -> bytes:
    """MQTT payload packing — msgpack {t, f} or stdlib fallback (identical info)."""
    if msgpack is not None:
        return msgpack.packb({b"t": t_ns, b"f": frame})
    return _FALLBACK.pack(_FALLBACK_MAGIC, t_ns) + frame


def unpack_csi(data: bytes):
    if data[:4] == _FALLBACK_MAGIC:
        _, t_ns = _FALLBACK.unpack_from(data)
        return t_ns, data[_FALLBACK.size:]
    if msgpack is None:
        raise ValueError("msgpack-packed payload but msgpack unavailable")
    d = msgpack.unpackb(data)
    return d[b"t"], d[b"f"]


class BridgeCore:
    def __init__(self, *, rx_id: int, raw_path, sink, on_event=None):
        self.rx_id = rx_id
        self.sink = sink
        self.on_event = on_event
        self._log = RawLogWriter(raw_path)
        self._parser = StreamParser()
        self._links = {}        # tx_idx -> LinkTracker
        self._unwrap = TimeUnwrapper()
        self.frames = 0
        self.rawframes = 0
        self.texts = 0
        self.topic = f"csi/rx{rx_id}"

    def ingest(self, t_ns: int, chunk: bytes):
        self._log.append(t_ns, chunk)
        for kind, val in self._parser.feed(chunk):
            if kind == "frame":
                self._on_frame(t_ns, val)
            elif kind == "rawframe":
                self.rawframes += 1
            elif kind == "text":
                self.texts += 1
                self._emit("text", val)
            elif kind == "crc_error":
                self._emit("crc_error", self._parser.crc_errors)
            elif kind == "junk":
                self._emit("junk", val)

    def _on_frame(self, t_ns, f):
        self.frames += 1
        _, ev = self._unwrap.update(boot_id=f.boot_id, t_us=f.esp_timer_us)
        if ev == "reboot":
            # RX board reboot — rebaseline to avoid mistaking the gap as RF loss (Section 5)
            for tr in self._links.values():
                tr.rebaseline()
            self._emit("reboot", f.boot_id)
        elif ev == "wrap":
            self._emit("wrap", self._unwrap.wraps)
        tr = self._links.setdefault(f.tx_idx, LinkTracker())
        tr.update(f.seq)
        self.sink.publish(self.topic, pack_csi(t_ns, f.raw))

    def _emit(self, kind, payload):
        if self.on_event:
            self.on_event(kind, payload)

    def status(self) -> dict:
        return {
            "rx_id": self.rx_id,
            "frames": self.frames,
            "rawframes": self.rawframes,
            "texts": self.texts,
            "crc_errors": self._parser.crc_errors,
            "junk_bytes": self._parser.junk_bytes,
            "wraps": self._unwrap.wraps,
            "reboots": self._unwrap.reboots,
            "links": {
                str(tx): {"rx": tr.received, "lost": tr.lost,
                          "resets": tr.resets, "loss": round(tr.loss_ratio, 5)}
                for tx, tr in sorted(self._links.items())
            },
        }

    def close(self):
        self._log.close()
