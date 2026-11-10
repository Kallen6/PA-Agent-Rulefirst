"""恒定节点闸门测试（§3.3 / §9.1）。"""
from __future__ import annotations

from pa_engine.gates.base import GATES, GateContext, run_all, run_gate


class _Frame:
    """最小帧替身：恒定节点不依赖任何行情数据。"""

    bars: tuple = ()
    indicators = None


def test_3_3_is_always_no() -> None:
    r = run_gate("3.3", _Frame(), GateContext())
    assert r.answer is False
    assert r.branch == "no"
    assert r.confidence == 1.0
    assert r.filled_by == "program"


def test_9_1_is_always_yes() -> None:
    r = run_gate("9.1", _Frame(), GateContext())
    assert r.answer is True
    assert r.branch == "yes"
    assert r.confidence == 1.0


def test_constant_nodes_registered() -> None:
    assert {"3.3", "9.1"} <= set(GATES)


def test_run_all_returns_sorted_trace_ready_results() -> None:
    results = run_all(_Frame(), GateContext())
    assert [r.node_id for r in results] == sorted(r.node_id for r in results)
    for r in results:
        trace = r.to_trace()
        assert trace["node_id"] == r.node_id
        assert trace["question"]
        assert trace["filled_by"] == "program"
