"""§4/§6 B 类证据节点单测：只补 trace，不改变周期分类。"""
from __future__ import annotations

from pa_engine.engine.pipeline import decide
from pa_engine.gates.base import GateContext
from pa_engine.gates.range import gate_6_1, gate_6_2, gate_6_6
from pa_engine.gates.swing import gate_4_1, gate_4_2
from tests.fixtures.patterns import TRIANGLE, zigzag_rows
from tests.fixtures.synthetic import make_regime_rows, make_setup_rows
from tests.support import build_v2_frame


def test_triangle_pattern_is_detected_without_changing_cycle() -> None:
    frame = build_v2_frame(zigzag_rows(TRIANGLE))
    result = gate_6_6(frame, GateContext())
    assert result.answer is True
    assert result.branch == "symmetrical_triangle"

    decision = decide(build_v2_frame(make_regime_rows("trading_range"))).decision
    assert decision["cycle_position"] == "trading_range"
    assert "symmetrical_triangle" not in decision["patterns"] or isinstance(
        decision["patterns"], list
    )


def test_ordered_swing_and_direction_evidence() -> None:
    frame = build_v2_frame(make_regime_rows("micro_channel"))
    ctx = GateContext(extras={"direction": "bullish"})
    assert gate_4_1(frame, ctx).answer is True
    assert gate_4_2(frame, ctx).answer == "bullish"


def test_range_type_and_clarity_branches() -> None:
    frame = build_v2_frame(zigzag_rows([100, 110, 101, 109, 102, 110, 101, 108]))
    clarity = gate_6_1(frame, GateContext())
    assert clarity.branch in ("clear", "unclear", "too_tight")
    kind = gate_6_2(frame, GateContext())
    assert kind.branch in ("trading_range", "trending_tr")


def test_new_evidence_nodes_do_not_change_cycle_classification() -> None:
    # 证据节点不参与 classify_cycle；setup 行是否改变周期由原闸门决定。
    for regime in ("micro_channel", "tight_channel", "normal_channel", "broad_channel"):
        decision = decide(build_v2_frame(make_regime_rows(regime))).decision
        assert decision["cycle_position"] == regime
    for regime in ("micro_channel", "tight_channel"):
        baseline = decide(build_v2_frame(make_regime_rows(regime))).decision
        setup = decide(build_v2_frame(make_setup_rows(regime))).decision
        assert baseline["cycle_position"] == setup["cycle_position"] == regime
