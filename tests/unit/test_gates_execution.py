"""执行段闸门测试：§9 入场、§10 风险、§11 下单路由。

原则：§10/§11 是纯算术/查表节点，必须 100% 可判定；§9.0 只做三分诊，
边界（score=2）允许进入灰区，因此这里同时断言「清晰过」与「清晰不过」。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.fixtures.synthetic import make_setup_rows
from tests.support import build_v2_frame

from pa_engine.engine.stance import apply_stance
from pa_engine.gates.base import GATES, GateContext, run_all
from pa_engine.gates.order import LIMIT_ORDER, NO_ORDER, STOP_ORDER, route_order
from pa_engine.gates.risk import (
    TradePlan,
    prepare_plan,
    risk_reward,
    widen_stop_for_rr_cap,
)

EXEC_NODES = ("9.0", "9.2", "9.3", "9.5", "10.1", "10.2", "10.3", "11.1", "11.2", "11.3", "11.4")


def _ctx(direction: str = "bullish", *, stance: str = "aggressive", **extras) -> GateContext:
    """默认用激进档，保持既有断言基于 RR≥1.0 / 方程余量≥0 的原始口径。"""
    ctx = GateContext()
    ctx.extras["direction"] = direction
    ctx.extras.update(extras)
    apply_stance(ctx, stance)
    return ctx


def _frame_with_signal(direction: str = "bullish"):
    return build_v2_frame(make_setup_rows("tight_channel", direction=direction))


def _bar(o: float, c: float, *, wick: float = 0.05, low_extra: float = 0.0, high_extra: float = 0.0) -> dict:
    return {
        "ts_open": 0,
        "open": o,
        "high": max(o, c) + wick + high_extra,
        "low": min(o, c) - wick - low_extra,
        "close": c,
        "volume": 1000.0,
    }


def _flat_rows(n: int = 30, price: float = 100.0, amp: float = 0.35) -> list[dict]:
    rows = []
    direction = 1
    for i in range(n):
        o = price
        c = o + amp * direction * (1 if i % 3 else -1)
        rows.append(_bar(o, c, wick=amp * 0.5))
        price = c
        if i % 4 == 3:
            direction *= -1
    return rows


def _entry_frame(k2: dict, k1: dict):
    rows = _flat_rows() + [k2, k1]
    for i, row in enumerate(rows):
        row["ts_open"] = 1_000_000 + i * 60_000
    return build_v2_frame(rows)


# ── 注册与 §9 入场 ────────────────────────────────────────────────────────────


def test_execution_nodes_registered() -> None:
    missing = [n for n in EXEC_NODES if n not in GATES]
    assert not missing, f"未注册的执行段节点：{missing}"


def test_9_2_aligned_and_opposite() -> None:
    frame = _frame_with_signal("bullish")
    ctx = _ctx("bullish")
    r = run_all(frame, ctx, nodes=["9.2"])[0]
    assert r.answer is True and r.branch == "aligned"

    bearish = _frame_with_signal("bearish")
    r2 = run_all(bearish, _ctx("bullish"), nodes=["9.2"])[0]
    assert r2.answer is False and r2.branch == "opposite"

    r3 = run_all(bearish, _ctx("neutral"), nodes=["9.2"])[0]
    assert r3.answer is False and r3.branch == "no_direction"


def test_9_3_too_long_threshold() -> None:
    ok_bar = _bar(100.0, 100.2, wick=0.02)
    long_bar = _bar(100.0, 101.5, wick=0.05)
    for bar, expected in ((ok_bar, False), (long_bar, True)):
        frame = _entry_frame(bar, _bar(bar["close"], bar["close"] + 0.1, wick=0.02))
        r = run_all(frame, _ctx("bullish"), nodes=["9.3"])[0]
        assert r.answer is expected, (r.evidence, expected)
        assert r.evidence["threshold_atr"] > 0


def test_9_5_follow_through() -> None:
    signal = _bar(100.0, 100.4, wick=0.03)
    follow = _bar(100.4, 100.8, wick=0.03)
    frame = _entry_frame(signal, follow)
    r = run_all(frame, _ctx("bullish"), nodes=["9.5"])[0]
    assert r.answer is True and r.branch == "yes"

    fail = _bar(100.4, 99.9, wick=0.03)
    frame2 = _entry_frame(signal, fail)
    r2 = run_all(frame2, _ctx("bullish"), nodes=["9.5"])[0]
    assert r2.answer is False and r2.branch == "failed"


def test_9_0_quality_three_way() -> None:
    strong = _bar(100.0, 100.8, wick=0.03)
    r = run_all(_entry_frame(strong, _bar(100.8, 101.1)), _ctx("bullish"), nodes=["9.0"])[0]
    assert r.answer is True and r.branch == "pass"
    assert r.evidence["score"] >= 3

    doji = _bar(100.0, 100.0, wick=0.35)
    r2 = run_all(_entry_frame(doji, _bar(100.0, 100.1)), _ctx("bullish"), nodes=["9.0"])[0]
    assert r2.answer is False and r2.branch == "fail"

    borderline = _bar(100.0, 100.1, wick=0.02, low_extra=0.15, high_extra=0.5)
    r3 = run_all(_entry_frame(borderline, _bar(100.1, 100.3)), _ctx("bullish"), nodes=["9.0"])[0]
    assert r3.evidence["score"] == 2
    assert r3.confidence < 0.8, "边界质量必须进入灰区"


# ── §10 风险 ─────────────────────────────────────────────────────────────────


def _plan(**overrides) -> TradePlan:
    base = dict(
        direction="bullish",
        entry=100.0,
        stop=99.5,
        tp1=103.0,
        tp2=105.0,
        estimated_win_rate=55.0,
        channel_width=8.0,
        range_width=20.0,
        atr=1.0,
        spike_height=8.0,
        tick=0.01,
        signal_bar_extreme=99.6,
        signal_bar_seq=2,
    )
    base.update(overrides)
    return TradePlan(**base)


def test_widen_stop_to_rr_cap() -> None:
    plan = _plan(spike_height=None)
    widened, evidence = widen_stop_for_rr_cap(plan)
    assert evidence["changed"] is True
    assert evidence["capped"] is False
    assert abs(risk_reward(widened) - 1.0) < 1e-6
    assert widened.stop == 97.0


def test_widen_stop_capped_by_spike_height() -> None:
    plan = _plan(spike_height=2.0)
    widened, evidence = widen_stop_for_rr_cap(plan)
    assert evidence["capped"] is True
    assert evidence["cap"] == 1.0
    assert widened.stop == 99.0, "外扩上限 = 尖峰高度 50%"
    assert risk_reward(widened) > 1.0


def test_10_1_stop_clarity() -> None:
    frame = _frame_with_signal("bullish")
    cases = [
        (99.59, "clear", True),
        (99.7, "inside_signal_bar", False),
        (100.5, "wrong_side", False),
    ]
    for stop, branch, expected in cases:
        ctx = _ctx("bullish")
        ctx.extras["trade_plan"] = _plan(stop=stop, signal_bar_extreme=99.6)
        r = run_all(frame, ctx, nodes=["10.1"])[0]
        assert r.answer is expected, (stop, r.evidence)
        assert r.branch == branch, (stop, r.branch)


def test_10_2_four_conditions() -> None:
    frame = _frame_with_signal("bullish")
    cases = {
        "rr_below_1": _plan(stop=99.9, tp1=100.05, estimated_win_rate=55.0, atr=100.0, channel_width=1000.0, range_width=1000.0),
        "channel_width": _plan(stop=98.0, tp1=102.0, channel_width=2.0, atr=100.0, range_width=1000.0),
        "range_width": _plan(stop=98.0, tp1=102.0, channel_width=1000.0, range_width=10.0, atr=100.0),
        "atr": _plan(stop=98.0, tp1=102.0, channel_width=1000.0, range_width=1000.0, atr=0.5),
    }
    for expected, plan in cases.items():
        ctx = _ctx("bullish")
        prepare_plan(plan, ctx)
        r = run_all(frame, ctx, nodes=["10.2"])[0]
        assert r.answer is True, (expected, r.evidence)
        assert r.branch == expected, (expected, r.branch)

    ctx = _ctx("bullish")
    prepare_plan(_plan(stop=99.6, tp1=102.0, channel_width=10.0, range_width=100.0, atr=2.0), ctx)
    ok = run_all(frame, ctx, nodes=["10.2"])[0]
    assert ok.answer is False and ok.branch == "ok"


def test_10_3_trader_equation() -> None:
    frame = _frame_with_signal("bullish")

    ctx = _ctx("bullish")
    prepare_plan(_plan(stop=99.5, tp1=100.5, estimated_win_rate=55.0), ctx)
    passed = run_all(frame, ctx, nodes=["10.3"])[0]
    assert passed.answer is True and passed.branch == "passed"
    assert passed.evidence["equation_left"] > passed.evidence["equation_right"]

    ctx2 = _ctx("bullish")
    prepare_plan(_plan(stop=99.5, tp1=100.5, estimated_win_rate=45.0), ctx2)
    failed = run_all(frame, ctx2, nodes=["10.3"])[0]
    assert failed.answer is False and failed.branch == "equation_failed"

    ctx3 = _ctx("bullish")
    prepare_plan(_plan(estimated_win_rate=0.0), ctx3)
    missing = run_all(frame, ctx3, nodes=["10.3"])[0]
    assert missing.answer is False and missing.branch == "missing_win_rate"
    assert missing.confidence < 0.8, "缺胜率必须进入灰区"


# ── §11 下单路由 ─────────────────────────────────────────────────────────────


def test_order_routing_table() -> None:
    cases = [
        (dict(cycle_position="spike", direction="bullish", spike_stage="ongoing"), NO_ORDER, "11.1"),
        (dict(cycle_position="spike", direction="bullish", spike_stage="ended", spike_path="flag"), STOP_ORDER, "11.1"),
        (dict(cycle_position="spike", direction="bullish", spike_stage="ended", spike_path="channel"), LIMIT_ORDER, "11.1"),
        (dict(cycle_position="micro_channel", direction="bullish"), LIMIT_ORDER, "11.2"),
        (dict(cycle_position="tight_channel", direction="bullish"), LIMIT_ORDER, "11.2"),
        (dict(cycle_position="normal_channel", direction="bullish"), STOP_ORDER, "11.2"),
        (dict(cycle_position="broad_channel", direction="bullish"), LIMIT_ORDER, "11.2"),
        (dict(cycle_position="trending_tr", direction="bullish"), STOP_ORDER, "11.2"),
        (dict(cycle_position="trading_range", direction="bullish", zone="lower_third"), LIMIT_ORDER, "11.3"),
        (dict(cycle_position="trading_range", direction="bullish", zone="middle_third"), NO_ORDER, "11.3"),
        (dict(cycle_position="trading_range", direction="bullish", breakout_quality="surviving"), STOP_ORDER, "11.4"),
        (dict(cycle_position="extreme_tr", direction="bullish"), NO_ORDER, "11.3"),
        (dict(cycle_position="unknown", direction="bullish"), NO_ORDER, ""),
        (dict(cycle_position="normal_channel", direction="neutral"), NO_ORDER, ""),
    ]
    for kwargs, order, node in cases:
        route = route_order(**kwargs)
        assert route.order_type == order, (kwargs, route)
        assert route.node_id == node, (kwargs, route)


def test_order_gates_mark_single_node() -> None:
    frame = _frame_with_signal("bullish")
    ctx = _ctx("bullish", cycle_position="normal_channel")
    results = {r.node_id: r for r in run_all(frame, ctx, nodes=["11.1", "11.2", "11.3", "11.4"])}
    assert results["11.2"].answer is True
    assert results["11.2"].branch == STOP_ORDER
    assert [n for n in ("11.1", "11.3", "11.4") if results[n].answer is True] == []


def test_spike_ongoing_forbids_order() -> None:
    frame = _frame_with_signal("bullish")
    ctx = _ctx(
        "bullish",
        cycle_position="spike",
        spike_stage="ongoing",
        spike_path="none",
    )
    results = {r.node_id: r for r in run_all(frame, ctx, nodes=["11.1", "11.2", "11.3", "11.4"])}
    assert results["11.1"].answer is True
    assert results["11.1"].branch == NO_ORDER
    assert any("禁止追单" in r.reason for r in results.values())
