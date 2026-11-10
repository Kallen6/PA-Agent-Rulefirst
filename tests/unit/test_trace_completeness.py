"""决策链路 trace 完整性：交易场景覆盖 §1–§14，N/A 节点可见。"""
from __future__ import annotations

from pa_engine.engine.pipeline import decide
from tests.fixtures.synthetic import make_regime_rows, make_setup_rows
from tests.support import build_v2_frame

STAGE1_REQUIRED = {
    "1.2", "1.3", "2.1", "2.2", "2.3", "2.4", "2.5",
    "3.1", "3.2", "3.3", "3.5", "3.6", "3.7",
    "4.1", "4.2",
    "5.1", "5.2", "5.3", "5.4", "5.5",
    "6.1", "6.2", "6.3", "6.4", "6.5", "6.6",
    "7.1", "7.2", "7.3", "7.4",
    "8.1", "8.2", "8.3",
}


def _trace(decision):
    return {item["node_id"]: item for item in decision["gate_trace"] + decision["decision_trace"]}


def test_signal_trade_trace_covers_stage1_and_entry() -> None:
    decision = decide(build_v2_frame(make_setup_rows("tight_channel"))).decision
    trace = _trace(decision)
    assert STAGE1_REQUIRED <= set(trace)
    assert {"9.0", "9.1", "9.2", "9.3", "9.4", "9.5", "9.6", "9.7"} <= set(trace)
    assert {"10.1", "10.2", "10.3", "14"} <= set(trace)
    assert trace["9.7"]["answer"] == "是"


def test_background_limit_trace_marks_9_7_na() -> None:
    decision = decide(build_v2_frame(make_regime_rows("broad_channel"))).decision
    trace = _trace(decision)
    assert trace["9.0P"]["answer"] == "是"
    assert trace["9.7"]["answer"] == "n/a"
    assert trace["14"]["answer"] == "否"


def test_no_duplicate_nodes_and_order_is_stable() -> None:
    decision = decide(build_v2_frame(make_regime_rows("micro_channel"))).decision
    ids = [item["node_id"] for item in decision["decision_trace"]]
    assert len(ids) == len(set(ids))
    assert ids.index("9.0") < ids.index("9.4") < ids.index("14")
    assert ids.index("10.1") < ids.index("10.3")


def test_wait_scenario_does_not_fabricate_entry_nodes() -> None:
    decision = decide(build_v2_frame(make_regime_rows("extreme_tr"))).decision
    trace = _trace(decision)
    assert decision["order_type"] == "不下单"
    assert decision["terminal"]["node_id"] == "1.3"
    assert "9.0" not in trace
