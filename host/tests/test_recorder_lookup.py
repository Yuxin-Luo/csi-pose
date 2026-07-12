"""Unit tests for RecorderCore segment lookup — see dev_doc/17 §4.4-§4.5."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "csi_pipe"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "capture"))

import store
import mqtt_recorder
import plan


def _make_core(effective_plan):
    """Construct RecorderCore + temporary SessionWriter, return (core, writer, path, tmpdir)."""
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "test.h5"
    writer = store.SessionWriter(path, meta={"session": "test"})
    core = mqtt_recorder.RecorderCore(writer, effective_plan=effective_plan)
    return core, writer, path, tmp


def test_lookup_segment_no_plan_returns_action_default():
    """No effective_plan passed → all frames treated as action (backward compat)."""
    core, writer, path, tmp = _make_core(effective_plan=None)
    try:
        seg_idx, state = core._lookup_segment(12345)
        assert seg_idx == 0
        assert state == 1
    finally:
        writer.close()
        tmp.cleanup()


def test_lookup_segment_before_set_recording_start_returns_action():
    """set_recording_start not called yet → treated as action (backward compat)."""
    eff = plan.expand_plan([(1, "stand", 30), (2, "squat", 30)])
    core, writer, path, tmp = _make_core(effective_plan=eff)
    try:
        seg_idx, state = core._lookup_segment(12345)
        assert seg_idx == 0
        assert state == 1
    finally:
        writer.close()
        tmp.cleanup()


def test_lookup_segment_in_action_section():
    """t_wall_ns falls in action segment → state=1."""
    eff = plan.expand_plan([(1, "stand", 30), (2, "squat", 30)])
    # 3 segments: stand(30) + transition(10) + squat(30) = 70s total
    core, writer, path, tmp = _make_core(effective_plan=eff)
    try:
        core.set_recording_start(1_000_000_000)  # t0 = 1s
        # t = 1 + 15 = 16s → falls in stand (0-30s)
        seg_idx, state = core._lookup_segment(16_000_000_000)
        assert seg_idx == 0  # stand segment
        assert state == 1     # action
    finally:
        writer.close()
        tmp.cleanup()


def test_lookup_segment_in_transition_section():
    """t_wall_ns falls in transition segment → state=0."""
    eff = plan.expand_plan([(1, "stand", 30), (2, "squat", 30)])
    core, writer, path, tmp = _make_core(effective_plan=eff)
    try:
        core.set_recording_start(1_000_000_000)
        # t = 1 + 35 = 36s → falls in transition (30-40s)
        seg_idx, state = core._lookup_segment(36_000_000_000)
        assert seg_idx == 1  # transition segment
        assert state == 0     # transition
    finally:
        writer.close()
        tmp.cleanup()


def test_lookup_segment_in_next_action_section():
    """t_wall_ns falls in second action segment → state=1, seg_idx=2."""
    eff = plan.expand_plan([(1, "stand", 30), (2, "squat", 30)])
    core, writer, path, tmp = _make_core(effective_plan=eff)
    try:
        core.set_recording_start(1_000_000_000)
        # t = 1 + 55 = 56s → falls in squat (40-70s)
        seg_idx, state = core._lookup_segment(56_000_000_000)
        assert seg_idx == 2  # squat segment
        assert state == 1     # action
    finally:
        writer.close()
        tmp.cleanup()
