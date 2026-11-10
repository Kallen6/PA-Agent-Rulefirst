"""§7 反转识别单测：仅诊断、前极点测试、二次入场计数。"""
from __future__ import annotations

from pa_engine.gates.base import GateContext
from pa_engine.gates.reversal import gate_7_1, gate_7_2, gate_7_3, gate_7_4
from tests.fixtures.patterns import REVERSAL_BULL, SECOND_ENTRY, zigzag_rows
from tests.support import build_v2_frame


def _frame(points, **kwargs):
    return build_v2_frame(zigzag_rows(points, **kwargs))


def test_trendline_break_plus_failed_extreme_is_reversal_attempt() -> None:
    frame = _frame(REVERSAL_BULL)
    ctx = GateContext(extras={"background_direction": "bullish"})
    result = gate_7_2(frame, ctx)
    assert result.answer is True
    assert result.branch == "reversal_attempt"
    assert result.confidence == 0.85
    assert result.evidence["trendline_broken"] is True
    assert result.evidence["failed_extreme_test"] is True
    assert ctx.extras["reversal_attempt"] is True


def test_new_extreme_keeps_trend_intact() -> None:
    frame = _frame([100, 106, 103, 110, 106, 112, 109])
    ctx = GateContext(extras={"background_direction": "bullish"})
    result = gate_7_2(frame, ctx)
    assert result.answer is False
    assert result.evidence["failed_extreme_test"] is False


def test_single_reversal_bar_in_bull_trend() -> None:
    rows = [
        {
            "ts_open": 1_000_000 + i * 60_000,
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.5 + i,
            "close": 100.8 + i,
            "volume": 1000.0,
        }
        for i in range(18)
    ]
    rows.append(
        {
            "ts_open": 2_200_000,
            "open": 118.5,
            "high": 118.8,
            "low": 114.0,
            "close": 114.5,
            "volume": 2000.0,
        }
    )
    frame = build_v2_frame(rows)
    ctx = GateContext(extras={"background_direction": "bullish"})
    result = gate_7_1(frame, ctx)
    assert result.answer is True
    assert result.branch == "single"
    assert result.evidence["reversal_bars"] == 1


def test_second_entry_count_and_failure_branch() -> None:
    frame = _frame(SECOND_ENTRY)
    ctx = GateContext(extras={"direction": "bearish", "background_direction": "bullish"})
    second = gate_7_3(frame, ctx)
    assert second.answer is True
    assert second.branch == "second"
    failed = gate_7_4(frame, ctx)
    assert failed.branch in ("failed", "intact")
    assert "invalidation" in failed.evidence


def test_second_entry_not_applicable_without_count() -> None:
    frame = _frame([100, 104, 102, 106])
    ctx = GateContext(extras={"direction": "bullish"})
    result = gate_7_4(frame, ctx)
    assert result.branch == "n/a"
    assert result.answer is False


def test_reversal_diagnosis_bans_counter_trend_order() -> None:
    from pa_engine.gates.guard import collect_violations

    frame = _frame(REVERSAL_BULL)
    ctx = GateContext(
        extras={
            "direction": "bearish",
            "background_direction": "bullish",
            "always_in": "bullish",
            "reversal_attempt": True,
            "cycle_position": "normal_channel",
            "entry_style": "signal",
            "stop_reference": "signal_bar",
        }
    )
    flags = collect_violations(frame, ctx)
    assert "counter_trend_reversal" in flags
    assert "counter_trend_always_in" in flags
