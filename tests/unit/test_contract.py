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


def test_legibility_default_and_invalid_fallback() -> None:
    # 缺省 → unknown（向后兼容旧客户端/记录）
    ans = parse_response('{"cycle_position":"spike","direction":"bullish","always_in":"bullish","signal_bar_quality":"pass"}')
    assert ans is not None and ans.market_legibility == "unknown"
    # 非法枚举 → unknown
    ans = parse_response('{"market_legibility":"看不懂"}')
    assert ans is not None and ans.market_legibility == "unknown"
    # 合法值保留
    ans = parse_response('{"market_legibility":"confused"}')
    assert ans is not None and ans.market_legibility == "confused"


def test_legibility_confused_revokes_plan() -> None:
    # 「看不懂」出口：confused → 撤销方案（wait），不推翻周期/方向
    from pa_engine.ai.client import MockLLM
    from pa_engine.engine.pipeline import decide
    from tests.fixtures.synthetic import make_regime_rows
    from tests.support import build_v2_frame

    llm = MockLLM({"market_legibility": "confused"})
    outcome = decide(build_v2_frame(make_regime_rows("broad_channel")), llm=llm, band=1.0)
    decision = outcome.decision
    assert outcome.llm_called is True
    assert decision["order_type"] == "不下单"
    assert decision["entry_price"] is None
    assert decision["terminal"]["node_id"] == "0.1"
    assert decision["terminal"]["outcome"] == "wait"
    # 周期/方向仍以代码为准
    assert decision["cycle_position"] == "broad_channel"


def test_legibility_clear_keeps_plan() -> None:
    from pa_engine.ai.client import MockLLM
    from pa_engine.engine.pipeline import decide
    from tests.fixtures.synthetic import make_regime_rows
    from tests.support import build_v2_frame

    llm = MockLLM({"market_legibility": "clear"})
    decision = decide(build_v2_frame(make_regime_rows("broad_channel")), llm=llm, band=1.0).decision
    assert decision["terminal"]["outcome"] == "trade"
    assert decision["entry_price"] is not None
