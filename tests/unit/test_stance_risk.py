"""四档档位对 §10.2 / §10.3 判定与外扩止损的影响（纯算术）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.fixtures.synthetic import make_setup_rows
from tests.support import build_v2_frame

from pa_engine.engine.stance import apply_stance
from pa_engine.gates.base import GateContext, run_all
from pa_engine.gates.risk import (
    TradePlan,
    prepare_plan,
    risk_reward,
    widen_stop_for_rr_cap,
)


def _frame():
    return build_v2_frame(make_setup_rows("tight_channel"))


def _plan(**overrides) -> TradePlan:
    base = dict(
        direction="bullish",
        entry=100.0,
        stop=99.5,
        tp1=100.5,
        tp2=101.0,
        estimated_win_rate=60.0,
        channel_width=1000.0,
        range_width=1000.0,
        atr=100.0,
        spike_height=None,
        tick=0.01,
        signal_bar_extreme=99.6,
        signal_bar_seq=2,
    )
    base.update(overrides)
    return TradePlan(**base)


def _ctx(stance: str, **extras) -> GateContext:
    ctx = GateContext()
    ctx.extras["direction"] = "bullish"
    ctx.extras.update(extras)
    apply_stance(ctx, stance)
    return ctx


def _run_risk(plan: TradePlan, stance: str, node: str):
    ctx = _ctx(stance)
    prepare_plan(plan, ctx)
    return run_all(_frame(), ctx, nodes=[node])[0]


def test_conservative_rejects_rr_at_one() -> None:
    # RR=1.0：保守档门槛 1.5 → §10.2 判「止损过大」
    result = _run_risk(_plan(), "conservative", "10.2")
    assert result.answer is True
    assert result.branch == "rr_below_1"
    assert result.evidence["min_rr"] == 1.5
    assert result.evidence["stance"] == "conservative"


def test_balanced_rejects_rr_below_its_floor() -> None:
    # RR=1.1：均衡档门槛 1.2 → 拒绝；激进档门槛 1.0 → 通过
    plan = _plan(stop=99.5, tp1=100.55)
    balanced = _run_risk(plan, "balanced", "10.2")
    aggressive = _run_risk(plan, "aggressive", "10.2")
    assert balanced.answer is True and balanced.branch == "rr_below_1"
    assert aggressive.answer is False and aggressive.branch == "ok"


def test_extreme_allows_slightly_negative_equation() -> None:
    # RR=1.0，胜率 49% → 方程余量约 -3.9%（落在 -5% 下限内）
    plan = _plan(estimated_win_rate=49.0)
    aggressive = _run_risk(plan, "aggressive", "10.3")
    extreme = _run_risk(plan, "extreme_aggressive", "10.3")
    assert aggressive.answer is False and aggressive.branch == "equation_failed"
    assert aggressive.evidence["equation_gap"] < 0
    assert extreme.answer is True and extreme.branch == "passed"
    assert extreme.evidence["min_equation_gap"] == -0.05


def test_conservative_requires_positive_equation_margin() -> None:
    # RR=1.5、胜率 55% → 方程余量约 +83% 远超 15%，保守档应通过 §10.3
    plan = _plan(stop=99.0, tp1=101.5, estimated_win_rate=55.0)
    result = _run_risk(plan, "conservative", "10.3")
    assert result.answer is True and result.branch == "passed"
    assert result.evidence["min_equation_gap"] == 0.15


def test_target_rr_drives_stop_widening() -> None:
    # 原始 RR=3.0；保守档外扩目标 1.55 → 止损变宽，RR 落到 1.55
    plan = _plan(stop=99.0, tp1=103.0)
    assert abs(risk_reward(plan) - 3.0) < 1e-9
    ctx = _ctx("conservative")
    widened = prepare_plan(plan, ctx)
    assert widened.widened is True
    assert abs(risk_reward(widened) - 1.55) < 1e-6
    assert ctx.extras["stop_widen"]["stance"] == "conservative"


def test_widen_default_keeps_legacy_target() -> None:
    # 无档位上下文（纯函数直接调用）仍以 RR=1.0 为目标，保持回放口径
    plan = _plan(stop=99.0, tp1=103.0)
    widened, evidence = widen_stop_for_rr_cap(plan)
    assert abs(risk_reward(widened) - 1.0) < 1e-6
    assert evidence["target_rr"] == 1.0


def test_min_win_rate_blocks_borderline_equation_winner() -> None:
    """P1：方程通过但胜率低于档位下限 → §10.3 拒；极度激进放行同一方案。

    构造：RR=1.25（均衡档 target，不外扩）、胜率 49%：
    方程余量 (49×0.525 − 51×0.5)/(51×0.5) ≈ +1% → 通过；
    均衡档 min_win_rate=50 → low_win_rate 拒。
    极度激进档外扩到 RR=1.05 后方程 +0.9%（≥ −5%）、胜率 49（≥ 30）→ 通过。
    """
    plan = _plan(stop=99.6, tp1=100.525, estimated_win_rate=49.0)
    balanced = _run_risk(plan, "balanced", "10.3")
    extreme = _run_risk(plan, "extreme_aggressive", "10.3")
    assert balanced.answer is False and balanced.branch == "low_win_rate"
    assert balanced.evidence["min_win_rate"] == 50.0
    assert extreme.answer is True and extreme.branch == "passed"
    assert extreme.evidence["min_win_rate"] == 30.0


def test_win_rate_floor_priority_after_equation() -> None:
    """分支优先级：RR 不足 → rr_below_1；方程不足 → equation_failed；胜率不足才 low_win_rate。"""
    plan = _plan(stop=99.6, tp1=100.525, estimated_win_rate=40.0)  # 方程负余量 + 胜率 40
    result = _run_risk(plan, "balanced", "10.3")
    assert result.branch == "equation_failed"  # 方程先于胜率下限拦截
