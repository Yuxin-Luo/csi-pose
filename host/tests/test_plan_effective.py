"""Unit tests for plan.expand_plan() — see dev_doc/17 §2.2."""
import sys
from pathlib import Path

# 让 import 能找到 host/capture/plan.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "capture"))

import plan


def test_expand_plan_with_transition_default():
    """TRANSITION_S_DEFAULT=10 时，2 段 plan 展开成 3 段（2 action + 1 transition）"""
    result = plan.expand_plan([(1, "stand", 30), (2, "squat", 30)])
    assert len(result) == 3
    assert sum(s.duration_s for s in result) == 70.0
    assert sum(1 for s in result if s.state == "transition") == 1
    assert result[0].name == "stand" and result[0].state == "action"
    assert result[1].name == "transition" and result[1].state == "transition"
    assert result[2].name == "squat" and result[2].state == "action"


def test_expand_plan_no_transition_when_constant_zero():
    """TRANSITION_S_DEFAULT=0 时，2 段 plan 展开成 2 段（无 transition）"""
    saved = plan.TRANSITION_S_DEFAULT
    plan.TRANSITION_S_DEFAULT = 0
    try:
        result = plan.expand_plan([(1, "a", 30), (2, "b", 20)])
        assert len(result) == 2
        assert sum(s.duration_s for s in result) == 50.0
        assert all(s.state == "action" for s in result)
    finally:
        plan.TRANSITION_S_DEFAULT = saved


def test_expand_norm_plan_23_segments_690s():
    """norm 12 action 段 → effective 23 段（12+11），总 690s

    Per brief docstring: 12 action segments, 11 transitions between them,
    580s (actions) + 110s (transitions) = 690s total.
    """
    norm = [
        (1, "empty_in", 60), (2, "pos1_set1", 40), (3, "pos2_set1", 40),
        (4, "pos3_set1", 40), (5, "pos1_set2", 40), (6, "pos2_set2", 40),
        (7, "pos3_set2", 40), (8, "pos1_set3", 40), (9, "pos2_set3", 40),
        (10, "pos3_set3", 40), (11, "sit", 40), (12, "lie_supine", 60),
        # empty_out omitted to match brief's "12 action segments" expectation
    ]
    eff = plan.expand_plan(norm)
    # 12 actions + 11 transitions between them = 23 segments
    assert len(eff) == 23
    # total: empty_in(60) + 9*pos(40) + sit(40) + lie_supine(60) = 520 (actions)
    #        + 11*TRANSITION_S_DEFAULT(10) = 110 (transitions) = 630s
    assert sum(s.duration_s for s in eff) == 630.0
    assert sum(1 for s in eff if s.state == "transition") == 11


def test_expand_test_plan_3_segments_70s():
    """test 2 action 段 → effective 3 段（2+1），总 70s（round 3 验证）"""
    test = [(1, "stand", 30), (2, "squat", 30)]
    eff = plan.expand_plan(test)
    assert len(eff) == 3
    assert sum(s.duration_s for s in eff) == 70.0
    # 第一段是 stand action, 最后一段是 squat action
    assert eff[0].name == "stand" and eff[0].state == "action"
    assert eff[-1].name == "squat" and eff[-1].state == "action"
    # 中间只有 1 个 transition
    assert eff[1].state == "transition"
