"""终态语义单测（v1 对齐：仅 §10.3 方程否决与 §14 禁令允许 reject）。"""
from __future__ import annotations

from pa_engine.ai.client import MockLLM
from pa_engine.engine.pipeline import _risk_blocker, _terminal_outcome_for
from pa_engine.gates.base import GateResult
from tests.fixtures.synthetic import make_regime_rows
from tests.support import build_v2_frame
from pa_engine.engine.pipeline import decide


def test_terminal_outcome_mapping() -> None:
    # §14 禁令与 §10.3 方程否决 → reject
    assert _terminal_outcome_for("14") == "reject"
    assert _terminal_outcome_for("10.3") == "reject"
    # §10.1/§10.2 失败 = 无有效方案，v1 语义必须 wait
    assert _terminal_outcome_for("10.1") == "wait"
    assert _terminal_outcome_for("10.2") == "wait"
    # 入口阻断（信号棒/背景限价失败）→ wait
    assert _terminal_outcome_for("9.0") == "wait"
    assert _terminal_outcome_for("9.0P") == "wait"
    assert _terminal_outcome_for("9.3") == "wait"


def test_risk_blocker_polarity() -> None:
    # 10.1/10.3 「是」=通过，10.2 「是」=止损过大
    results = [
        GateResult("10.1", True, "ok", 0.9, {}, "止损明确"),
        GateResult("10.2", False, "ok", 0.9, {}, "止损不大"),
        GateResult("10.3", False, "ok", 0.9, {}, "方程未过"),
    ]
    fail = _risk_blocker(results)
    assert fail is not None and fail.node_id == "10.3"
    assert _terminal_outcome_for(fail.node_id) == "reject"


def test_llm_win_rate_rejection_is_reject() -> None:
    # §10.3 被方程否决 → reject（v1 语义允许）
    rows = make_regime_rows("broad_channel")
    llm = MockLLM({"estimated_win_rate": 5, "win_rate_reason": "结构弱"})
    decision = decide(build_v2_frame(rows), llm=llm, band=1.0, stance="aggressive").decision
    assert decision["terminal"]["node_id"] == "10.3"
    assert decision["terminal"]["outcome"] == "reject"
