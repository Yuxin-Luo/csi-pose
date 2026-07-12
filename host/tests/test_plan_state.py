"""Unit tests for plan.PlanState with effective_plan (segments list)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "capture"))

import plan


def test_plan_state_initial_uses_first_segment():
    """new PlanState(segments) → cur_state 等于第一段 state"""
    segments = plan.expand_plan([(1, "stand", 30), (2, "squat", 30)])
    # 3 段: stand(action), transition, squat(action)
    ps = plan.PlanState(segments=segments)
    assert ps.cur_state == "action"
    assert ps.cur_label == "stand"
    assert ps.cur_duration == 30.0


def test_plan_state_tick_crosses_action_to_transition():
    """elapsed > action 段 duration → tick() 推进到 transition 段"""
    segments = plan.expand_plan([(1, "stand", 30), (2, "squat", 30)])
    ps = plan.PlanState(segments=segments)
    # 初始 seg_start = None，第一次 tick 设置 seg_start
    assert ps.seg_start is None
    changed = ps.tick(100.0)
    assert changed is False  # 第一次 tick 只设 seg_start, 不算段切换
    assert ps.seg_start == 100.0
    # elapsed=30.5 时进入 transition 段
    changed = ps.tick(130.5)
    assert changed is True
    assert ps.cur_state == "transition"
    assert ps.cur_label == "transition"
    assert ps.cur_duration == 10.0


def test_plan_state_tick_crosses_transition_to_next_action():
    """elapsed > transition 段 duration → tick() 推进到下一 action 段"""
    segments = plan.expand_plan([(1, "stand", 30), (2, "squat", 30)])
    ps = plan.PlanState(segments=segments)
    ps.tick(0.0)               # 初始化
    ps.tick(35.0)              # → transition
    assert ps.cur_state == "transition"
    changed = ps.tick(45.0)    # → squat
    assert changed is True
    assert ps.cur_state == "action"
    assert ps.cur_label == "squat"
