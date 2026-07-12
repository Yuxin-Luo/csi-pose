"""Unit test for plan.draw_overlay_transition() — see dev_doc/17 §5.4."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "capture"))

import plan


def test_draw_overlay_transition_writes_magenta_box():
    """在 transition 段绘制亮品红 box（与黄色 action 状态区分）"""
    segments = plan.expand_plan([(1, "stand", 30), (2, "squat", 30)])
    ps = plan.PlanState(segments=segments)
    ps.tick(0.0)
    ps.tick(35.0)             # → transition
    assert ps.cur_state == "transition"

    frame = np.zeros((360, 640, 3), dtype=np.uint8)  # 黑色底
    out = plan.draw_overlay_transition(frame, ps, elapsed_sec=38.5)

    # 返回值就是 frame 本身（in-place 修改）
    assert out is frame
    # 抽样：右上角区域应该有亮品红像素（BGR(255,0,255)）
    # box 在右上 (x0, y0) 附近，size 取决于文字宽度
    h, w = frame.shape[:2]
    sample = frame[15:25, w-200:w-50, :]  # 右上角区域
    # 至少有一些像素是亮品红 (B=255, G=0, R=255)
    magenta_pixels = np.sum(
        (sample[:, :, 0] == 255) & (sample[:, :, 1] == 0) & (sample[:, :, 2] == 255)
    )
    assert magenta_pixels > 100, f"期望 ≥100 个亮品红像素，实际 {magenta_pixels}"
