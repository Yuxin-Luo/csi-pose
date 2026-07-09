"""Serial wire format ('Wire Format Agreement' section — single source of truth).

CSI frame 130B LE: magic u16=0xC51D | rx_id u8 | tx_idx u8 | seq u32 |
esp_timer_us u32 | rssi i8 | noise_floor i8 | len u8=56 | boot_id u8 |
iq i8[112] | crc16 u16 (CCITT-FALSE, [0,128) range).

RAW dump frame: header 18B (magic 0xC51F ... buf_len u16) + buf + crc16.
esp_timer_us is u32 — 71.58 min wrap, host unwrap uses csi_host.unwrap.
"""
import struct
from dataclasses import dataclass

from .crc16 import crc16_ccitt

FRAME_MAGIC, RAW_MAGIC, FRAME_LEN = 0xC51D, 0xC51F, 130
PAYLOAD_MAGIC = 0xC51E
_FMT = "<HBBIIbbBB112s"                            # + crc u16 separate (total 130B)
_MAGIC_BYTES = struct.pack("<H", FRAME_MAGIC)      # b"\x1d\xc5"
_RAW_MAGIC_BYTES = struct.pack("<H", RAW_MAGIC)    # b"\x1f\xc5"
RAW_HDR = struct.Struct("<HBBIIbbBBH")             # 18B header, then buf + crc16
RAW_BUF_MAX = 512


@dataclass
class CsiFrame:
    rx_id: int
    tx_idx: int
    seq: int
    esp_timer_us: int
    rssi: int
    noise_floor: int
    len: int
    boot_id: int
    iq: bytes
    raw: bytes


@dataclass
class RawFrame:
    rx_id: int
    tx_idx: int
    seq: int
    esp_timer_us: int
    rssi: int
    noise_floor: int
    flags: int          # b0=first_word_invalid, b1..2=sig_mode
    boot_id: int
    buf_len: int
    buf: bytes
    raw: bytes


def build_frame(*, rx_id, tx_idx, seq, esp_timer_us, rssi, noise_floor, boot_id, iq):
    iq = bytes(iq)
    if len(iq) != 112:
        raise ValueError("iq must be 112 bytes")
    body = struct.pack(_FMT, FRAME_MAGIC, rx_id, tx_idx, seq, esp_timer_us,
                       rssi, noise_floor, 56, boot_id, iq)
    return body + struct.pack("<H", crc16_ccitt(body))


def parse_frame(buf: bytes):
    if len(buf) != FRAME_LEN:
        return None
    if crc16_ccitt(buf[:128]) != struct.unpack_from("<H", buf, 128)[0]:
        return None
    m, rx, tx, seq, t, rssi, nf, ln, bid, iq = struct.unpack(_FMT, buf[:128])
    if m != FRAME_MAGIC:
        return None
    return CsiFrame(rx, tx, seq, t, rssi, nf, ln, bid, iq, buf)


class StreamParser:
    """Byte stream -> ("frame"|"rawframe"|"text"|"junk"|"crc_error", x) events.

    Allows mixing of text mode lines (boot banner, STAT) and binary frames,
    and resyncs by advancing 2 bytes on CRC failure.
    """

    MAX_PENDING_TEXT = 4096

    def __init__(self):
        self._buf = bytearray()
        self.crc_errors = 0
        self.junk_bytes = 0

    def feed(self, data: bytes):
        self._buf += data
        out = []
        while True:
            i = self._next_magic()
            if i is None:
                self._drain_lines(out)
                break
            if i:
                self._drain_text(i, out)
            if not self._parse_at_head(out):
                break
        return out

    def _next_magic(self):
        c = [j for j in (self._buf.find(_MAGIC_BYTES),
                         self._buf.find(_RAW_MAGIC_BYTES)) if j != -1]
        return min(c) if c else None

    def _parse_at_head(self, out):
        """Try parsing at buffer head. Returns False if more bytes needed."""
        if self._buf[:2] == _MAGIC_BYTES:
            if len(self._buf) < FRAME_LEN:
                return False
            f = parse_frame(bytes(self._buf[:FRAME_LEN]))
            if f is not None:
                out.append(("frame", f))
                del self._buf[:FRAME_LEN]
            else:
                self.crc_errors += 1
                out.append(("crc_error", 1))
                del self._buf[:2]
            return True
        if len(self._buf) < RAW_HDR.size:
            return False
        hdr = RAW_HDR.unpack_from(self._buf)
        buf_len = hdr[9]
        if buf_len > RAW_BUF_MAX:
            self.junk_bytes += 2
            out.append(("junk", 2))
            del self._buf[:2]
            return True
        total = RAW_HDR.size + buf_len + 2
        if len(self._buf) < total:
            return False
        blob = bytes(self._buf[:total])
        if crc16_ccitt(blob[:-2]) == struct.unpack_from("<H", blob, total - 2)[0]:
            _, rx, tx, seq, t, rssi, nf, fl, bid, bl = hdr
            out.append(("rawframe", RawFrame(rx, tx, seq, t, rssi, nf, fl, bid, bl,
                                             blob[RAW_HDR.size:-2], blob)))
            del self._buf[:total]
        else:
            self.crc_errors += 1
            out.append(("crc_error", 1))
            del self._buf[:2]
        return True

    def _drain_text(self, n, out):
        """Emit leading n bytes (guaranteed frame boundary) as lines of text/junk."""
        chunk = bytes(self._buf[:n])
        del self._buf[:n]
        for piece in chunk.split(b"\n"):
            piece = piece.strip(b"\r")
            if not piece:
                continue
            try:
                s = piece.decode("ascii")
            except UnicodeDecodeError:
                s = None
            if s is not None and s.isprintable():
                out.append(("text", s))
            else:
                self.junk_bytes += len(piece)
                out.append(("junk", len(piece)))

    def _drain_lines(self, out):
        """No magic — emit completed lines only, preserve remainder (flush as junk if too large)."""
        nl = self._buf.rfind(b"\n")
        if nl != -1:
            self._drain_text(nl + 1, out)
        if len(self._buf) > self.MAX_PENDING_TEXT:
            n = len(self._buf) - 1
            self.junk_bytes += n
            out.append(("junk", n))
            del self._buf[:n]
