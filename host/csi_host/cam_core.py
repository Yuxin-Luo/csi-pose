# """CamCore — frame timestamp → cam/meta publish (sender side; video storage handled by CLI).
# sink injection (bridge NullSink/MqttSink duck typing) enables testing without cv2·MQTT."""
# Translation: CamCore — frame timestamp → cam/meta publish (sender side; video storage handled by CLI). sink injection (bridge NullSink/MqttSink duck typing) enables testing without cv2·MQTT.
try:
    import msgpack
except ImportError:
    msgpack = None


class CamCore:
    """Core logic for publishing webcam frame timestamps to cam/meta topic.

    Parameters
    ----------
    sink:
        ``publish(topic, payload)`` duck type. NullSink or MqttSink.
    on_event:
        Optional callback ``(kind: str, val) -> None`` — for extension, currently unused.
    """
    # """Core logic for publishing webcam frame timestamps to cam/meta topic.
    # Parameters: sink (publish duck type), on_event (optional callback).

    def __init__(self, sink, *, on_event=None):
        if msgpack is None:
            raise RuntimeError("cam_capture requires msgpack -- pip install msgpack")
        self._sink = sink
        self._on_event = on_event
        self._frames = 0        # Successful handle_frame count
        self._drops = 0         # Cumulative drop count
        self._frame_idx = 0     # Next frame_idx to publish (monotonically increasing)
        self._consec_drops = 0  # Consecutive drop counter — reset to 0 on successful handle_frame

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def handle_frame(self, t_ns) -> int:
        """Publish frame grab timestamp to cam/meta and return the assigned frame_idx.

        Parameters
        ----------
        t_ns:
            Host clock immediately after grab (``time.time_ns()``).

        Returns
        -------
        int
            frame_idx assigned to this frame (0, monotonically increasing).
        """
        # """Publish frame grab timestamp to cam/meta and return the assigned frame_idx."""
        idx = self._frame_idx
        payload = msgpack.packb({"frame_idx": idx, "t_ns": int(t_ns)})
        self._sink.publish("cam/meta", payload)
        self._frames += 1
        self._frame_idx += 1
        self._consec_drops = 0  # Success → reset consecutive drops
        return idx

    def note_drop(self) -> int:
        """Record frame read failure and return current consecutive drop count.

        Returns
        -------
        int
            Current consecutive drop count (reset on successful handle_frame).
            CLI uses this value to determine if threshold exceeded.
        """
        # """Record frame read failure and return current consecutive drop count."""
        self._drops += 1
        self._consec_drops += 1
        return self._consec_drops

    def status(self) -> dict:
        """Return current aggregated status.

        Returns
        -------
        dict
            ``{"frames": int, "drops": int, "frame_idx": int}``
        """
        # """Return current aggregated status."""
        return {
            "frames": self._frames,
            "drops": self._drops,
            "frame_idx": self._frame_idx,
        }
