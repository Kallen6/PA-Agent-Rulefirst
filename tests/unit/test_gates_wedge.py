"""§8 楔形 / 三推单测：结构识别、回撤/反转分类、抛物线禁令。"""
from __future__ import annotations

from pa_engine.gates.base import GateContext
from pa_engine.gates.guard import collect_violations
from pa_engine.gates.wedge import assess_wedge, gate_8_1, gate_8_2, gate_8_3
from tests.fixtures.patterns import (
    PARABOLIC_BULL,
    WEDGE_BEAR,
    WEDGE_BULL,
    zigzag_rows,
)
from tests.support import build_v2_frame


def _frame(points):
    return build_v2_frame(zigzag_rows(points))


def test_rising_wedge_three_push() -> None:
    frame = _frame(WEDGE_BULL)
    result = gate_8_1(frame, GateContext())
    assert result.answer is True
    assert result.branch == "three_push"
    assert result.evidence["decay"] is True
    assert result.evidence["contraction"] <= 0.75


def test_falling_wedge_direction_and_pullback_classification() -> None:
    frame = _frame(WEDGE_BEAR)
    ctx = GateContext(extras={"background_direction": "bullish"})
    assert assess_wedge(frame, ctx).wedge_direction == "bearish"
    result = gate_8_2(frame, ctx)
    assert result.answer == "pullback"
    assert result.branch == "pullback"


def test_wedge_same_as_main_trend_is_reversal_risk() -> None:
    frame = _frame(WEDGE_BULL)
    ctx = GateContext(extras={"background_direction": "bullish"})
    result = gate_8_2(frame, ctx)
    assert result.answer == "reversal"
    assert result.confidence == 0.6
    assert ctx.extras["wedge_type"] == "reversal"

    ctx.extras["cycle_position"] = "normal_channel"
    ctx.extras["direction"] = "bullish"
    ctx.extras["stop_reference"] = "structure"
    flags = collect_violations(frame, ctx)
    assert "wedge_reversal_chase" in flags


def test_parabolic_wedge_is_flagged_even_without_decay() -> None:
    frame = _frame(PARABOLIC_BULL)
    ctx = GateContext()
    assert assess_wedge(frame, ctx).parabolic is True
    result = gate_8_3(frame, ctx)
    assert result.answer is True
    assert result.branch == "parabolic"
    assert ctx.extras["parabolic"] is True


def test_normal_wedge_is_not_parabolic() -> None:
    frame = _frame(WEDGE_BULL)
    result = gate_8_3(frame, GateContext())
    assert result.answer is False
    assert result.branch == "normal"


def test_no_alternating_structure_returns_na() -> None:
    frame = _frame([100, 101, 102, 103, 104])
    result = gate_8_2(frame, GateContext())
    assert result.branch == "n/a"
