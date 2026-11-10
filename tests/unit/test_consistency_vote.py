"""自一致性投票单测：多数决议、分歧降级、失败容错、代码周期不变。"""
from __future__ import annotations

from typing import Any

from pa_engine.ai.client import MockLLM
from pa_engine.engine.consistency import CONSERVATIVE, vote_gray_zone
from pa_engine.engine.pipeline import decide
from tests.fixtures.synthetic import make_regime_rows
from tests.support import build_v2_frame


class _SequenceLLM:
    """按顺序返回预设答案；Exception 实例表示该次调用失败。"""

    def __init__(self, answers: list[Any]) -> None:
        self.answers = list(answers)
        self.calls = 0

    def complete(self, prompt: str) -> dict[str, Any]:
        item = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return dict(item)


def _answer(cycle: str = "tight_channel", **overrides: Any) -> dict[str, Any]:
    base = {
        "cycle_position": cycle,
        "direction": "bullish",
        "always_in": "bullish",
        "signal_bar_quality": "pass",
        "confidence": 80,
        "estimated_win_rate": 58,
    }
    base.update(overrides)
    return base


def _gray_frame():
    return build_v2_frame(make_regime_rows("broad_channel"))


def test_majority_wins_and_records_agreement() -> None:
    llm = _SequenceLLM([_answer("tight_channel"), _answer("tight_channel"), _answer("spike")])
    result = vote_gray_zone(llm, "prompt", samples=3)
    assert result.valid == 3
    assert result.answer["cycle_position"] == "tight_channel"
    assert result.disagreements == ()
    assert result.degraded == ()
    assert 0.6 <= result.agree_ratio < 1.0
    record = result.as_record()
    assert record["samples"] == 3 and record["valid"] == 3


def test_full_disagreement_degrades_cycle_to_unknown() -> None:
    llm = _SequenceLLM([_answer("tight_channel"), _answer("spike"), _answer("trading_range")])
    result = vote_gray_zone(llm, "prompt", samples=3)
    assert result.answer["cycle_position"] == CONSERVATIVE["cycle_position"] == "unknown"
    assert "cycle_position" in result.disagreements
    assert "cycle_position" in result.degraded
    assert result.agree_ratio < 0.6


def test_partial_failure_uses_valid_votes() -> None:
    llm = _SequenceLLM([TimeoutError("超时"), _answer("tight_channel"), _answer("tight_channel")])
    result = vote_gray_zone(llm, "prompt", samples=3)
    assert result.valid == 2
    assert len(result.errors) == 1
    assert result.answer["cycle_position"] == "tight_channel"
    assert "TimeoutError" in result.errors[0]


def test_all_failed_returns_empty_answer() -> None:
    llm = _SequenceLLM([TimeoutError("超时")])
    result = vote_gray_zone(llm, "prompt", samples=3)
    assert result.valid == 0
    assert result.answer == {}
    assert result.agree_ratio == 0.0
    assert len(result.errors) == 3


def test_numeric_fields_use_median() -> None:
    llm = _SequenceLLM(
        [
            _answer(estimated_win_rate=50),
            _answer(estimated_win_rate=60),
            _answer(estimated_win_rate=90),
        ]
    )
    result = vote_gray_zone(llm, "prompt", samples=3)
    assert result.answer["estimated_win_rate"] == 60.0


def test_pipeline_vote_keeps_code_cycle() -> None:
    frame = _gray_frame()
    baseline = decide(frame).decision["cycle_position"]
    llm = _SequenceLLM([_answer("spike"), _answer("spike"), _answer("spike")])
    outcome = decide(frame, llm=llm, vote=3)
    assert outcome.decision["cycle_position"] == baseline
    assert outcome.decision["vote"]["samples"] == 3
    assert outcome.decision["vote"]["valid"] == 3
    assert llm.calls == 3
    assert outcome.prompt_stats["sha256"]


def test_pipeline_vote_disagreement_does_not_touch_code_decision() -> None:
    frame = _gray_frame()
    baseline = decide(frame).decision
    llm = _SequenceLLM([_answer("spike"), _answer("tight_channel"), _answer("trading_range")])
    outcome = decide(frame, llm=llm, vote=3)
    decision = outcome.decision
    assert decision["cycle_position"] == baseline["cycle_position"]
    assert decision["direction"] == baseline["direction"]
    assert decision["gate_result"] == baseline["gate_result"]
    assert decision["order_type"] == baseline["order_type"]
    # 感知层自己的周期字段被降级，记录里可见
    assert decision["llm_review"]["cycle_position"] == "unknown"
    assert "cycle_position" in decision["vote"]["degraded"]


def test_clear_market_never_votes() -> None:
    frame = build_v2_frame(make_regime_rows("micro_channel"))
    llm = MockLLM()
    outcome = decide(frame, llm=llm, vote=3)
    assert outcome.gray_zone.need_llm is False
    assert llm.call_count == 0
    assert "vote" not in outcome.decision


def test_vote_all_failed_records_llm_error() -> None:
    frame = _gray_frame()
    llm = _SequenceLLM([TimeoutError("超时")])
    outcome = decide(frame, llm=llm, vote=2)
    assert outcome.llm_called is False
    assert outcome.decision["vote"]["valid"] == 0
    assert "llm_error" in outcome.decision
    # 感知层失败保留代码决策（含 §9.0P 限价方案）
    assert outcome.decision["terminal"]["outcome"] in ("trade", "wait", "reject")
