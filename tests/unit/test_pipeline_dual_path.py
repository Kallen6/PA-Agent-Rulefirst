"""双路径流水线与 LLM 覆盖单测。"""
from __future__ import annotations

from pa_engine.ai.client import MockLLM
from pa_engine.engine.pipeline import decide
from tests.fixtures.synthetic import make_regime_rows, make_setup_rows
from tests.support import build_v2_frame


def _decide(regime: str, setup: bool = False, llm=None, band=None):
    rows = make_setup_rows(regime) if setup else make_regime_rows(regime)
    kwargs = {"llm": llm} if llm is not None else {}
    if band is not None:
        kwargs["band"] = band
    return decide(build_v2_frame(rows), **kwargs)


def test_signal_path_trade_has_full_prices() -> None:
    decision = _decide("micro_channel").decision
    assert decision["entry_style"] == "signal"
    assert decision["terminal"]["outcome"] == "trade"
    assert decision["entry_price"] is not None
    assert decision["stop_loss_price"] < decision["entry_price"]
    assert decision["take_profit_price"] > decision["entry_price"]
    assert decision["take_profit_price_2"] > decision["take_profit_price"]


def test_background_limit_trade_uses_structure_stop() -> None:
    decision = _decide("broad_channel").decision
    assert decision["terminal"]["outcome"] == "trade"
    assert decision["entry_style"] == "background_limit"
    assert decision["entry_rule"].startswith("结构位限价")
    assert decision["stop_reference"] == "structure"


def test_llm_win_rate_override_can_revoke_plan() -> None:
    llm = MockLLM({"estimated_win_rate": 5, "win_rate_reason": "结构弱"})
    decision = _decide("broad_channel", llm=llm, band=1.0).decision
    assert decision["order_type"] == "不下单"
    assert decision["terminal"]["node_id"] == "10.3"
    assert decision["llm_review"]["estimated_win_rate"] == 5


def test_llm_win_rate_override_keeps_valid_plan() -> None:
    llm = MockLLM({"estimated_win_rate": 95, "win_rate_reason": "边界清晰"})
    decision = _decide("broad_channel", llm=llm, band=1.0).decision
    assert decision["terminal"]["outcome"] == "trade"
    assert decision["estimated_win_rate"] == 95
    assert decision["estimated_win_rate_reasoning"] == "边界清晰"


def test_llm_wedge_reversal_revokes_chase() -> None:
    llm = MockLLM({"wedge_type": "reversal"})
    decision = _decide("broad_channel", llm=llm, band=1.0).decision
    assert decision["order_type"] == "不下单"
    assert decision["terminal"]["node_id"] == "8.2"


def test_llm_parabolic_revokes_chase() -> None:
    llm = MockLLM({"wedge_type": "parabolic"})
    decision = _decide("broad_channel", llm=llm, band=1.0).decision
    assert decision["terminal"]["node_id"] == "8.3"


def test_llm_failure_keeps_code_decision() -> None:
    class Broken:
        def complete(self, prompt: str) -> dict:
            raise TimeoutError("boom")

    outcome = _decide("broad_channel", llm=Broken())
    decision = outcome.decision
    assert outcome.llm_called is False
    assert "TimeoutError" in decision["llm_error"]
    # 代码仍能独立给出背景限价方案，不被 LLM 失败阻断
    assert decision["entry_style"] == "background_limit"
    assert decision["terminal"]["outcome"] == "trade"
