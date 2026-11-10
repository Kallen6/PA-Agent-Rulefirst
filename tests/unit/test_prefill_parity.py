"""预填（方向 / AlwaysIn / 三窗口）与 v1 的 parity 测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.support import V1_OK, build_dual_frames, make_raw_rows

if V1_OK:
    from pa_agent.ai.decision_nodes import judge_always_in as v1_always_in
    from pa_agent.ai.decision_nodes import judge_direction as v1_direction
    from pa_agent.ai.trend_context import build_trend_context as v1_trend
    from pa_engine.prefill.always_in import judge_always_in as v2_always_in
    from pa_engine.prefill.direction import judge_direction as v2_direction
    from pa_engine.prefill.trend_context import build_trend_context as v2_trend


def _skip() -> bool:
    return not V1_OK


def _answer_to_bool(answer: str) -> bool | None:
    if answer == "是":
        return True
    if answer == "否":
        return False
    return None


def test_direction_parity() -> None:
    if _skip():
        print("  [skip] v1 不可用")
        return
    frame_v1, frame_v2 = build_dual_frames(make_raw_rows(seed=11))
    d1, _ = v1_direction(frame_v1)
    d2 = v2_direction(frame_v2)
    assert d1 == d2.direction, f"v1={d1} v2={d2.direction}"


def test_always_in_parity() -> None:
    if _skip():
        print("  [skip] v1 不可用")
        return
    frame_v1, frame_v2 = build_dual_frames(make_raw_rows(seed=13))
    f1 = v1_always_in(frame_v1)
    r2 = v2_always_in(frame_v2)
    assert _answer_to_bool(str(f1.answer)) == r2.answer
    assert (f1.branch or None) == (r2.branch or None)


def test_trend_context_parity() -> None:
    if _skip():
        print("  [skip] v1 不可用")
        return
    frame_v1, frame_v2 = build_dual_frames(make_raw_rows(seed=17))
    direction = "bullish"
    t1 = v1_trend(frame_v1, direction)
    t2 = v2_trend(frame_v2, direction)
    for key in ("background_direction", "trading_direction", "conflict", "relationship", "recent_spike"):
        assert t1[key] == t2[key], f"{key}: v1={t1[key]} v2={t2[key]}"
