"""Unit tests for SessionWriter segment support — see dev_doc/17 §3.4."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "csi_pipe"))

import h5py
import store


def test_session_writer_creates_segment_datasets():
    """SessionWriter() 自动创建 video/segment_idx + video/state datasets"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.h5"
        writer = store.SessionWriter(path, meta={"session": "test"})
        writer.close()

        with h5py.File(path, "r") as h:
            assert "video/segment_idx" in h
            assert "video/state" in h
            assert h["video/segment_idx"].dtype == "uint32"
            assert h["video/state"].dtype == "uint8"


def test_append_video_writes_seg_idx_and_state():
    """append_video(t_ns, frame_idx, seg_idx, state) 同时写 segment 标记"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.h5"
        writer = store.SessionWriter(path, meta={"session": "test"})
        writer.append_video(1000, 0, seg_idx=0, state=1)
        writer.append_video(1033, 1, seg_idx=0, state=1)
        writer.append_video(1066, 2, seg_idx=1, state=0)  # transition
        writer.close()

        with h5py.File(path, "r") as h:
            seg_idx = h["video/segment_idx"][...]
            state = h["video/state"][...]
            assert list(seg_idx) == [0, 0, 1]
            assert list(state) == [1, 1, 0]


def test_update_segment_writes_meta_segments_on_close():
    """update_segment() 累积到 _segments_meta，close() 写入 meta/segments"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.h5"
        writer = store.SessionWriter(path, meta={"session": "test"})
        writer.update_segment(start_t_ns=0, end_t_ns=30_000_000_000,
                              name="stand", state="action")
        writer.update_segment(start_t_ns=30_000_000_000, end_t_ns=40_000_000_000,
                              name="transition", state="transition")
        writer.update_segment(start_t_ns=40_000_000_000, end_t_ns=70_000_000_000,
                              name="squat", state="action")
        writer.close()

        with h5py.File(path, "r") as h:
            segments = json.loads(h["meta"].attrs["segments"])
            assert len(segments) == 3
            assert segments[0]["name"] == "stand" and segments[0]["state"] == "action"
            assert segments[1]["name"] == "transition" and segments[1]["state"] == "transition"
            assert segments[2]["name"] == "squat" and segments[2]["state"] == "action"
            # 时间戳精确
            assert segments[0]["start_t_ns"] == 0
            assert segments[1]["start_t_ns"] == 30_000_000_000
