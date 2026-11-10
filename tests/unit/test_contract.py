"""感知层输出契约测试。"""
from __future__ import annotations

from pa_engine.ai.contract import ALLOWED_CYCLE_POSITIONS, GrayZoneAnswer, parse_response


def test_parse_valid_json() -> None:
    text = '{"cycle_position":"tight_channel","direction":"bullish","always_in":"bullish","signal_bar_quality":"pass","qualitative_notes":"ok"}'
    ans = parse_response(text)
    assert isinstance(ans, GrayZoneAnswer)
    assert ans.cycle_position == "tight_channel"
    assert ans.direction == "bullish"


def test_parse_invalid_enum_falls_back() -> None:
    text = '{"cycle_position":"descending_triangle","direction":"up","always_in":"maybe","signal_bar_quality":"great"}'
    ans = parse_response(text)
    assert ans is not None
    assert ans.cycle_position == "unknown"
    assert ans.direction == "neutral"
    assert ans.always_in == "none"
    assert ans.signal_bar_quality == "weak"


def test_parse_non_json_returns_none() -> None:
    assert parse_response("这不是 JSON") is None


def test_allowed_positions_count() -> None:
    assert len(ALLOWED_CYCLE_POSITIONS) == 9
