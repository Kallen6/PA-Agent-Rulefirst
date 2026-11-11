"""T0.5 阈值单一来源与 v1 常量对齐。

v1 只读引用；v1 不可用时显式跳过（不静默通过）。
"""
from __future__ import annotations

from unittest import SkipTest

from pa_engine.config import thresholds as t
from tests.support import V1_OK


def _require_v1() -> None:
    if not V1_OK:
        raise SkipTest("v1 (PA_Agent) 不可用，无法做阈值对齐")


def test_v1_kline_constants_match() -> None:
    _require_v1()
    from pa_agent.ai import kline_features as kf

    assert t.Bar.VOLUME_LOOKBACK == kf._VOLUME_LOOKBACK
    assert t.Bar.VOLUME_HIGH_RATIO == kf._VOLUME_HIGH_RATIO
    assert t.Bar.VOLUME_LOW_RATIO == kf._VOLUME_LOW_RATIO


def test_v1_market_feature_constants_match() -> None:
    _require_v1()
    from pa_agent.ai import market_features as mf

    assert t.Structure.LOOKBACK == mf._DEFAULT_LOOKBACK
    assert t.Structure.OVERLAP_WINDOW == mf._OVERLAP_WINDOW
    assert t.Structure.BARBWIRE_OVERLAP_MIN == mf._BARBWIRE_OVERLAP_THRESHOLD
    assert t.Structure.BARBWIRE_WIDTH_ATR_MAX == mf._BARBWIRE_WIDTH_ATR_MAX
    assert t.Structure.BARBWIRE_SCORE_MIN == mf._BARBWIRE_SCORE_THRESHOLD
    assert t.Structure.BREAKOUT_RECLAIM_BARS == mf._BREAKOUT_RECLAIM_BARS
    assert t.Structure.BREAKOUT_TEST_TOLERANCE_ATR == mf._BREAKOUT_TEST_TOLERANCE_ATR
    assert t.Structure.SURVIVING_MIN_BARS == mf._SURVIVING_MIN_BARS
    assert t.Structure.SPIKE_LOOKBACK == mf._SPIKE_LOOKBACK
    assert t.Structure.SPIKE_MIN_RUN == mf._SPIKE_MIN_RUN


def test_v1_decision_node_constants_match() -> None:
    _require_v1()
    from pa_agent.ai import decision_nodes as dn

    assert t.Prefill.BAR_COUNT_MIN == dn.BAR_COUNT_THRESHOLD
    assert t.Prefill.DIRECTION_WINDOW == dn.DIRECTION_WINDOW
    assert t.Prefill.DIRECTION_WINDOW_MED == dn.DIRECTION_WINDOW_MED
    assert t.Prefill.DIRECTION_STRONG_SHORT_SCORE == dn.DIRECTION_STRONG_SHORT_SCORE
    assert t.Prefill.DIRECTION_BULL_THRESHOLD == dn.DIRECTION_BULL_THRESHOLD
    assert t.Prefill.DIRECTION_BEAR_THRESHOLD == dn.DIRECTION_BEAR_THRESHOLD
    assert t.Prefill.DIRECTION_EMA_SLOPE_ATR == dn.DIRECTION_EMA_SLOPE_ATR_RATIO
    assert t.Prefill.DIRECTION_COG_ATR == dn.DIRECTION_COG_ATR_RATIO
    assert t.Prefill.TREND_BAR_DOMINANCE_RATIO == dn.TREND_BAR_DOMINANCE_RATIO
    assert t.Prefill.OVERLAP_LOW == dn.OVERLAP_LOW_THRESHOLD
    assert t.Prefill.OVERLAP_HIGH == dn.OVERLAP_HIGH_THRESHOLD
    assert t.Prefill.EMA_SLOPE_LOOKBACK == dn.EMA_SLOPE_LOOKBACK
    assert t.Prefill.ALWAYS_IN_NEAR_WINDOW == dn.ALWAYS_IN_NEAR_WINDOW
    assert t.Prefill.ALWAYS_IN_WINDOW == dn.ALWAYS_IN_WINDOW
    assert t.Prefill.ALWAYS_IN_NEAR_SAME_SIDE == dn.ALWAYS_IN_NEAR_SAME_SIDE_RATIO
    assert t.Prefill.ALWAYS_IN_SAME_SIDE == dn.ALWAYS_IN_SAME_SIDE_RATIO
    assert t.Prefill.ALWAYS_IN_PULLBACK_ATR == dn.ALWAYS_IN_PULLBACK_ATR_RATIO
    assert t.Prefill.SIGNAL_BAR_LONG_ATR == dn.SIGNAL_BAR_LONG_ATR_RATIO


def test_new_threshold_groups_are_complete() -> None:
    # 新增 B 类节点不得在闸门里散落字面量：关键常量必须存在且区间合理
    assert 0 < t.Entry.BACKGROUND_LIMIT_MIN_ATR < t.Entry.BACKGROUND_LIMIT_MAX_ATR
    assert t.Entry.FIRST_ENTRY_MAX_HL < t.Entry.SECOND_ENTRY_MIN_HL < t.Entry.THIRD_ENTRY_MIN_HL
    assert 0 < t.Reversal.REVERSAL_CLOSE_POS < 1
    assert 0 < t.Wedge.CONTRACTION_RATIO <= 1
    assert t.Wedge.PARABOLIC_SLOPE_RATIO > 1
    assert 0 < t.Triangle.CONTRACTION_RATIO <= 1
    assert t.Range.BOUNDARY_TOUCH_MIN >= 2
    assert t.Range.CLEAR_WIDTH_MIN_ATR < t.Range.CLEAR_WIDTH_MAX_ATR
