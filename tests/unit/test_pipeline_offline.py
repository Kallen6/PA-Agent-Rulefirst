"""端到端离线测试：清晰行情 0 调用、灰区 1 次 Mock 调用、失败可解释。

验收点：
1. 微通道/紧凑通道在代码路径产出完整三价（stop<entry<tp1<tp2）且 0 次 LLM；
2. 尖峰进行中、极端区间走 v1 短路语义：不下单且不调用 LLM；
3. 灰区（宽通道重叠贴近阈值）只调用一次 Mock，prompt ≤10KB；
4. Mock 复核 signal_bar_quality=fail 时能撤销方案；
5. 数据不足/重复运行/记录 schema 均稳定。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.fixtures.synthetic import REGIMES, make_regime_rows, make_setup_rows
from tests.support import build_v2_frame

from pa_engine.ai.client import MockLLM
from pa_engine.ai.contract import ALLOWED_CYCLE_POSITIONS
from pa_engine.config.thresholds import GrayZone as GZ
from pa_engine.engine.pipeline import decide
from pa_engine.records.writer import build_record_payload

OUTCOMES = {"trade", "wait", "reject", "unknown"}


def _frame(kind: str, *, setup: bool = False, n: int = 100):
    rows = make_setup_rows(kind) if setup else make_regime_rows(kind, n=n)
    return build_v2_frame(rows)


def test_clear_market_makes_zero_llm_calls() -> None:
    frame = _frame("micro_channel")
    llm = MockLLM()
    outcome = decide(frame, llm=llm)
    decision = outcome.decision
    assert outcome.gray_zone.need_llm is False
    assert llm.call_count == 0
    assert decision["order_type"] != "不下单"
    assert decision["terminal"]["outcome"] == "trade"
    assert decision["stop_loss_price"] < decision["entry_price"] < decision["take_profit_price"]
    assert decision["take_profit_price"] < decision["take_profit_price_2"]
    assert decision["estimated_win_rate"] and decision["estimated_win_rate"] > 50


def test_tight_channel_setup_trades_without_llm() -> None:
    outcome = decide(_frame("tight_channel", setup=True), llm=MockLLM())
    decision = outcome.decision
    assert outcome.llm_called is False
    assert decision["cycle_position"] == "tight_channel"
    assert decision["order_type"] == "限价单"
    assert decision["terminal"]["outcome"] == "trade"
    assert decision["order_direction"] == "做多"


def test_spike_ongoing_short_circuits() -> None:
    llm = MockLLM()
    outcome = decide(_frame("spike"), llm=llm)
    decision = outcome.decision
    assert decision["gate_result"] == "proceed"
    assert decision["order_type"] == "不下单"
    assert decision["terminal"]["node_id"] == "11.1"
    assert "禁止追单" in decision["reason"]
    assert llm.call_count == 0


def test_extreme_tr_is_wait_without_llm() -> None:
    llm = MockLLM()
    outcome = decide(_frame("extreme_tr"), llm=llm)
    decision = outcome.decision
    assert decision["gate_result"] == "wait"
    assert decision["terminal"]["node_id"] == "1.3"
    assert decision["order_type"] == "不下单"
    assert llm.call_count == 0


def test_gray_market_calls_llm_once_within_budget() -> None:
    frame = _frame("broad_channel")
    llm = MockLLM()
    outcome = decide(frame, llm=llm)
    assert outcome.gray_zone.need_llm is True
    assert llm.call_count == 1
    assert outcome.llm_called is True
    assert outcome.prompt_stats["bytes"] <= GZ.MAX_PROMPT_BYTES
    assert llm.calls[0]["bytes"] == outcome.prompt_stats["bytes"]
    assert "llm_review" in outcome.decision
    assert outcome.decision["llm_review"]["signal_bar_quality"] == "pass"


def test_llm_fail_review_cancels_plan() -> None:
    frame = _frame("tight_channel", setup=True)
    llm = MockLLM({"signal_bar_quality": "fail", "qualitative_notes": "信号棒质量差"})
    outcome = decide(frame, llm=llm, band=1.0)  # 强制全量灰区
    decision = outcome.decision
    assert llm.call_count == 1
    assert decision["order_type"] == "不下单"
    assert decision["entry_price"] is None
    assert decision["terminal"]["node_id"] == "9.0"


class _BrokenLLM:
    def complete(self, prompt: str) -> dict:
        raise TimeoutError("模拟超时")


def test_llm_failure_does_not_break_pipeline() -> None:
    frame = _frame("broad_channel")
    outcome = decide(frame, llm=_BrokenLLM(), stance="aggressive")
    decision = outcome.decision
    assert outcome.gray_zone.need_llm is True
    assert outcome.llm_called is False
    assert "llm_error" in decision and "TimeoutError" in decision["llm_error"]
    # §9.0P 修复后，宽通道无信号棒也能给出背景限价方案；
    # LLM 失败只记录错误，不应撤销代码决策（v1 语义：感知层失败保留程序判定）。
    assert decision["entry_style"] == "background_limit"
    assert decision["terminal"]["outcome"] == "trade"


def test_insufficient_data_is_explicit_unknown() -> None:
    llm = MockLLM()
    outcome = decide(_frame("normal_channel", n=6), llm=llm)
    decision = outcome.decision
    assert decision["gate_result"] == "unknown"
    assert decision["cycle_position"] == "unknown"
    assert decision["terminal"]["node_id"] == "1.1"
    assert llm.call_count == 0


def test_all_regimes_produce_valid_shape() -> None:
    for kind in REGIMES:
        outcome = decide(_frame(kind))
        decision = outcome.decision
        assert decision["cycle_position"] in ALLOWED_CYCLE_POSITIONS, kind
        assert decision["terminal"]["outcome"] in OUTCOMES, kind
        assert decision["gate_trace"], kind
        if decision["gate_result"] == "proceed":
            assert decision["decision_trace"], kind
        assert decision["direction"] in ("bullish", "bearish", "neutral")
        assert 0 <= decision["diagnosis_confidence"] <= 100
        for node in ("gate_trace", "decision_trace"):
            for entry in decision[node]:
                assert {"node_id", "question", "answer", "branch", "confidence"} <= set(entry)


def test_decision_is_deterministic() -> None:
    frame = _frame("normal_channel")
    first = decide(frame).decision
    second = decide(frame).decision
    assert first == second


def test_record_payload_schema() -> None:
    frame = _frame("tight_channel", setup=True)
    outcome = decide(frame, llm=MockLLM())
    payload = build_record_payload(outcome, symbol="TEST", timeframe="1h")
    assert payload["schema_version"] == "v2"
    assert payload["symbol"] == "TEST"
    assert payload["decision"]["entry_price"] is not None
    assert "gate_evidence" in payload
    assert payload["gray_zone"]["need_llm"] is False
    assert payload["prompt_stats"]["bytes"] == 0
    assert payload["llm_called"] is False
    for node_id, evidence in payload["gate_evidence"].items():
        assert isinstance(node_id, str)
        assert isinstance(evidence, dict)
