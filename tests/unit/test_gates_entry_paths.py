"""§9.0P / §9.4 / §9.6 / §9.7 与双路径路由单测。"""
from __future__ import annotations

from pa_engine.engine.pipeline import decide
from pa_engine.gates.base import GateContext
from pa_engine.gates.entry import gate_9_0p, gate_9_4, gate_9_6, gate_9_7
from pa_engine.gates.guard import collect_violations
from tests.fixtures.synthetic import make_regime_rows
from tests.support import build_v2_frame


def _decide(regime: str, setup: bool = False):
    """固定 aggressive 档：本文件验证路径判定，需保持原始 RR≥1.0 口径。"""
    from tests.fixtures.synthetic import make_setup_rows

    rows = make_setup_rows(regime) if setup else make_regime_rows(regime)
    return decide(build_v2_frame(rows), stance="aggressive")


def test_background_limit_path_is_a_real_second_entry_route() -> None:
    outcome = _decide("broad_channel")
    decision = outcome.decision
    assert decision["entry_style"] == "background_limit"
    assert decision["order_type"] != "不下单"
    assert decision["terminal"]["outcome"] == "trade"
    assert decision["stop_reference"] == "structure"
    assert decision["entry_price"] > decision["stop_loss_price"]
    nodes = {r["node_id"]: r for r in decision["decision_trace"]}
    assert nodes["9.0P"]["answer"] == "是"
    assert nodes["9.7"]["answer"] == "n/a"


def test_signal_bar_path_keeps_signal_semantics() -> None:
    decision = _decide("tight_channel", setup=True).decision
    assert decision["entry_style"] == "signal"
    assert decision["stop_reference"] == "signal_bar"
    assert decision["order_type"] != "不下单"
    nodes = {r["node_id"]: r for r in decision["decision_trace"]}
    assert nodes["9.7"]["answer"] == "是"


def test_both_entry_paths_failing_waits() -> None:
    decision = _decide("tight_channel").decision
    assert decision["order_type"] == "不下单"
    assert decision["terminal"]["node_id"] == "9.0P"
    nodes = {r["node_id"]: r for r in decision["decision_trace"]}
    # 激进档（pass=2）把边界信号棒判为合格 → 信号路径被 §9.2 否决后
    # 回退 §9.0P（P0 配套），回退也失败 → 终态记 9.0P 的失败原因
    assert nodes["9.0"]["answer"] == "是"
    assert nodes["9.0P"]["answer"] == "否"


def test_9_0p_rejects_counter_trend_limit() -> None:
    frame = build_v2_frame(make_regime_rows("broad_channel"))
    ctx = GateContext(
        extras={
            "cycle_position": "broad_channel",
            "direction": "bullish",
            "always_in": "bearish",
            "background_direction": "bearish",
            "zone": "lower_third",
        }
    )
    result = gate_9_0p(frame, ctx)
    assert result.answer is False
    assert result.branch == "side_conflict"


def test_9_0p_rejects_range_middle() -> None:
    frame = build_v2_frame(make_regime_rows("trading_range"))
    ctx = GateContext(
        extras={
            "cycle_position": "trading_range",
            "direction": "bullish",
            "always_in": "bullish",
            "zone": "middle_third",
        }
    )
    result = gate_9_0p(frame, ctx)
    assert result.answer is False
    assert result.branch in ("middle_third", "barbwire", "no_structure")


def test_entry_count_nodes_and_third_attempt_guard() -> None:
    frame = build_v2_frame(make_regime_rows("micro_channel"))
    ctx = GateContext(extras={"direction": "bullish", "cycle_position": "micro_channel"})
    first = gate_9_4(frame, ctx)
    assert first.answer is True
    assert first.branch == "first"
    second = gate_9_6(frame, ctx)
    assert second.answer is False
    assert second.branch == "first"

    # 直接注入 HL 计数达到第三次尝试，验证 §14 守卫
    import pa_engine.gates.guard as guard_module

    class _FakeSF:
        hl_count = type(
            "HL",
            (),
            {"bull_count": 3, "bear_count": 0},
        )()

    flagged = GateContext(
        extras={
            "direction": "bullish",
            "cycle_position": "micro_channel",
            "entry_style": "signal",
            "stop_reference": "signal_bar",
            "zone": "lower_third",
        }
    )
    original = guard_module.structure_of
    guard_module.structure_of = lambda _frame, _ctx: _FakeSF()
    try:
        flags = collect_violations(frame, flagged)
    finally:
        guard_module.structure_of = original
    assert "third_attempt" in flags


def test_9_7_is_na_on_background_limit() -> None:
    ctx = GateContext(extras={"entry_style": "background_limit", "direction": "bullish"})
    result = gate_9_7(build_v2_frame(make_regime_rows("broad_channel")), ctx)
    assert result.answer == "n/a"
    assert result.branch == "n/a"
