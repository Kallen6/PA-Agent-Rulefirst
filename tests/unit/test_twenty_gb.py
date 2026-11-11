"""20GB 门控单测（v1 文件20：连续≥20根未触EMA 绝对不逆势 + 首触非反转）。"""
from __future__ import annotations

from pa_engine.config.thresholds import Prefill
from pa_engine.gates.base import GateContext
from pa_engine.gates.guard import collect_violations
from pa_engine.prefill.always_in import judge_always_in
from tests.support import build_v2_frame


def _make_rows(n_flat: int = 30, n_rise: int = 30, step: float = 4.0) -> list[dict]:
    """前段平盘，后段强单边上涨（整根远离 EMA）。"""
    rows = []
    price = 100.0
    ts = 1_000_000
    for i in range(n_flat):
        rows.append(
            {
                "ts_open": ts + i * 60_000,
                "open": price,
                "high": price + 0.4,
                "low": price - 0.4,
                "close": price + 0.05,
                "volume": 1000.0,
            }
        )
        price += 0.05
    for j in range(n_rise):
        open_ = price
        close = open_ + step
        rows.append(
            {
                "ts_open": ts + (n_flat + j) * 60_000,
                "open": open_,
                "high": close + 0.1,
                "low": open_ - 0.1,
                "close": close,
                "volume": 1000.0,
            }
        )
        price = close
    return rows


def test_twenty_gb_detected_and_forces_direction() -> None:
    frame = build_v2_frame(_make_rows())
    result = judge_always_in(frame)
    assert result.twenty_gb is True
    assert result.twenty_gb_side == "bullish"
    # 绝对不逆势：方向强制为 20GB 一侧
    assert result.always_in == "bullish"
    assert "20GB" in result.strength
    assert "20GB" in result.reason


def test_no_twenty_gb_on_mixed_market() -> None:
    from tests.fixtures.synthetic import make_regime_rows

    frame = build_v2_frame(make_regime_rows("trading_range"))
    result = judge_always_in(frame)
    assert result.twenty_gb is False
    assert result.twenty_gb_side is None


def test_guard_twenty_gb_counter() -> None:
    frame = build_v2_frame(_make_rows())
    ctx = GateContext(
        extras={
            "direction": "bearish",
            "always_in": "bearish",  # 模拟普通 AlwaysIn 未命中 20GB 侧的场景
            "twenty_gb": True,
            "twenty_gb_side": "bullish",
            "cycle_position": "normal_channel",
            "zone": "unknown",
            "entry_style": "signal",
        }
    )
    flags = collect_violations(frame, ctx)
    assert "twenty_gb_counter" in flags
    # 同向时不触发
    ctx2 = GateContext(extras=dict(ctx.extras, direction="bullish"))
    assert "twenty_gb_counter" not in collect_violations(frame, ctx2)


def test_first_touch_suppresses_reversal_attempt() -> None:
    """20GB 首次触 EMA：即使代码判确认反转，也不标 reversal_attempt。"""
    import pa_engine.gates.reversal as rev
    from pa_engine.gates.reversal import ReversalAssessment

    confirmed = ReversalAssessment(
        trend="bullish",
        single_bar=False,
        reversal_bars=0,
        trendline_broken=True,
        failed_extreme_test=True,
        confirmed=True,
        line_value=90.0,
        prior_extreme=95.0,
        latest_extreme=94.0,
        confidence=0.85,
        reason="趋势线破+前极点测试失败 → 反转尝试（仅诊断）",
    )
    ctx = GateContext(extras={"twenty_gb": False, "twenty_gb_first_touch": True})
    orig = rev.assess_reversal
    rev.assess_reversal = lambda f, c: confirmed
    try:
        result = rev.gate_7_2(None, ctx)
    finally:
        rev.assess_reversal = orig
    assert result.answer is False
    assert ctx.extras["reversal_attempt"] is False
    assert "首次触 EMA" in result.reason


def test_first_touch_not_triggered_without_20gb_history() -> None:
    """K1 触 EMA 但此前不足 20 根未触 → 不标记首触。"""
    from tests.fixtures.synthetic import make_regime_rows

    frame = build_v2_frame(make_regime_rows("tight_channel"))
    result = judge_always_in(frame)
    assert result.twenty_gb_first_touch is False


def test_twenty_gb_threshold_constant() -> None:
    assert Prefill.TWENTY_GB_MIN_BARS == 20
