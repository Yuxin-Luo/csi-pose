"""RecorderCore — MQTT messages -> SessionWriter.

MQTT client is injected (BridgeCore pattern) — just call handle(topic, payload).
CRC is re-verified in parse_frame (transport path is not trusted)."""
from csi_host.bridge_core import unpack_csi
from csi_host.framing import parse_frame
from csi_host.gap import LinkTracker
from csi_host.unwrap import TimeUnwrapper

try:
    import msgpack
except ImportError:
    msgpack = None


# Default topic list the recorder subscribes to — wire_client default and external reference constant.
SUBSCRIPTIONS = [("csi/#", 0), ("cam/meta", 0)]


def wire_client(client, on_message, *, subscriptions=None, log=None):
    """Install callbacks on the client. Call before loop_start()
    (CONNACK is processed in the network loop, so both before and after connect are fine).

    Subscriptions must be done inside on_connect to guarantee re-subscription after broker
    restart — calling subscribe() directly where connect() is called once will not restore
    subscriptions after reconnect, resulting in silent zero-frame sessions.

    Supports both paho 1.x (4-arg) and 2.x VERSION2 (5-arg) signatures.
    Exceptions must not be raised inside _on_connect: paho re-propagates callback exceptions,
    causing the network thread to die silently (bypassing the recorder's _error_flag path).
    """
    _subs = subscriptions if subscriptions is not None else SUBSCRIPTIONS

    def _on_connect(client, userdata, flags, rc, properties=None):
        # paho calls on_connect even on rc!=0 (connection refused) — subscription is meaningless when refused.
        # rc is int in 1.x, ReasonCode in 2.x, but ReasonCode.__eq__ supports int comparison,
        # so rc != 0 is compatible on both sides.
        if rc != 0:
            if log is not None:
                log(f"[rec] on_connect rejected rc={rc} — skipping re-subscription")
            return
        # Re-subscribe on every connect/reconnect — key to broker restart tolerance
        client.subscribe(_subs)
        if log is not None:
            log(f"[rec] on_connect: re-subscribed {_subs}")

    client.on_connect = _on_connect
    client.on_message = on_message


class RecorderCore:
    def __init__(self, writer, *, on_event=None, effective_plan=None):
        self.writer = writer
        self.on_event = on_event
        self._unwrap = {}    # rx_id -> TimeUnwrapper
        self._links = {}     # (rx,tx) -> LinkTracker
        self.frames = 0
        self.crc_drops = 0
        self.cam_frames = 0
        self.cam_errors = 0
        self.unknown = 0
        self.reboots = 0
        self.wraps = 0       # u32 wrap cumulative count (per-rx sum)
        # dev_doc/17 §4.5: segment lookup dependencies
        self._effective_plan = effective_plan or []
        self._t0_wall_ns = None  # set by set_recording_start() after gate opens

    def set_recording_start(self, t_wall_ns: int):
        """dev_doc/17 §4.5: called by recorder.py after start-on-key gate opens;
        injects wall-clock t0 so _lookup_segment can map cam frames to segments."""
        self._t0_wall_ns = int(t_wall_ns)

    def _lookup_segment(self, t_ns: int) -> tuple:
        """Look up which segment a video frame belongs to by wall-clock t_ns.

        Returns:
            (seg_idx, state) — seg_idx is effective_plan index, state is 0/1 (transition/action).
            No plan / not started / t_ns < t0_wall_ns: treated as action (backward compat).
        """
        if not self._effective_plan or self._t0_wall_ns is None:
            return 0, 1
        elapsed_s = (t_ns - self._t0_wall_ns) / 1e9
        if elapsed_s < 0:
            return 0, 1
        cum = 0.0
        for i, seg in enumerate(self._effective_plan):
            cum += seg.duration_s
            if elapsed_s < cum:
                return i, (0 if seg.state == "transition" else 1)
        return len(self._effective_plan) - 1, 1

    def handle(self, topic, payload, t_recv_ns=0):
        # t_recv_ns: recorder receive time — bridge-stamped host time (included in unpack_csi payload)
        # is used as auxiliary reference only; it is NOT used for the storage path.
        if topic.startswith("csi/"):
            self._on_csi(payload)
        elif topic == "cam/meta":
            self._on_cam(payload)
        elif topic.startswith("sys/"):
            pass                                    # Heartbeat — not stored
        else:
            self.unknown += 1

    def _on_csi(self, payload):
        try:
            t_ns, raw = unpack_csi(payload)
            f = parse_frame(raw)            # CRC re-verification included — returns None or exception on failure
        except Exception:
            self.crc_drops += 1
            return
        if f is None:
            self.crc_drops += 1
            return
        uw = self._unwrap.setdefault(f.rx_id, TimeUnwrapper())
        u, ev = uw.update(boot_id=f.boot_id, t_us=f.esp_timer_us)
        if ev == "reboot":
            self.reboots += 1
            for (i, _), tr in self._links.items():
                if i == f.rx_id:
                    tr.rebaseline()
            self._emit("reboot", (f.rx_id, f.boot_id))
        elif ev == "wrap":
            self.wraps += 1
        tr = self._links.setdefault((f.rx_id, f.tx_idx), LinkTracker())
        tr.update(f.seq)
        self.writer.append(rx_id=f.rx_id, tx_idx=f.tx_idx, t_ns=t_ns, esp_us=u,
                           iq=f.iq, seq=f.seq, rssi=f.rssi, noise=f.noise_floor,
                           boot_id=f.boot_id)
        self.frames += 1

    def _on_cam(self, payload):
        if msgpack is None:
            self.cam_errors += 1
            return
        try:
            d = msgpack.unpackb(payload)
            if not isinstance(d, dict):
                raise ValueError("cam/meta is not a dict")
            t = d.get("t_ns", d.get(b"t_ns"))
            fi = d.get("frame_idx", d.get(b"frame_idx"))
            if t is None or fi is None:
                raise ValueError("t_ns/frame_idx missing")
            # dev_doc/17 §4.4: look up segment + state for this video frame
            seg_idx, state = self._lookup_segment(int(t))
            self.writer.append_video(int(t), int(fi), seg_idx=seg_idx, state=state)
            self.cam_frames += 1
        except Exception:
            self.cam_errors += 1

    def _emit(self, kind, val):
        if self.on_event:
            self.on_event(kind, val)

    def status(self):
        # Links key is "rx-tx" — bridge is 1-bridge=1rx so only tx is needed as key,
        # recorder is multi-rx so needs both
        return {
            "frames": self.frames, "crc_drops": self.crc_drops,
            "cam_frames": self.cam_frames, "cam_errors": self.cam_errors,
            "unknown": self.unknown, "reboots": self.reboots,
            "wraps": self.wraps,
            "links": {f"{k[0]}-{k[1]}": {"rx": tr.received, "lost": tr.lost,
                                         "resets": tr.resets,
                                         "loss": round(tr.loss_ratio, 5)}
                      for k, tr in sorted(self._links.items())},
        }
