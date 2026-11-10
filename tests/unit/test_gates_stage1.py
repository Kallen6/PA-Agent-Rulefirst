"""Stage1 闸门（§3/§5/§6）与预填的离线测试。

覆盖：
1. 节点注册完整性（缺一即需要 LLM，属于静默降级风险）；
2. 8 类合成行情下的状态签名（每类至少一个决定性节点）；
3. 通道回撤深度排序与阈值边界；
4. 尖峰三判据（重叠/位移/方向）能排除回撤与区间内推进；
5. 数据不足/指标缺失时不抛异常、不臆造答案。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.fixtures.synthetic import REGIMES, make_regime_rows
from tests.support import build_v2_frame

from pa_engine.gates.base import GATES, GateContext, run_all
from pa_engine.gates.channel import _channel_metrics
from pa_engine.prefill import build_trend_context, judge_always_in, judge_direction

STAGE1_NODES = (
    "3.1", "3.2", "3.3", "3.5", "3.6", "3.7",
    "5.1", "5.2", "5.3", "5.4", "5.5",
    "6.3", "6.4", "6.5",
)


def _ctx_for(frame) -> GateContext:
    """闸门独立运行时注入方向，模拟流水线预填。"""
    ctx = GateContext()
    ctx.extras["direction"] = judge_direction(frame).direction
    return ctx


def _run(kind: str):
    frame = build_v2_frame(make_regime_rows(kind))
    ctx = _ctx_for(frame)
    results = {r.node_id: r for r in run_all(frame, ctx, nodes=STAGE1_NODES)}
    return frame, ctx, results


def test_all_stage1_nodes_registered() -> None:
    missing = [n for n in STAGE1_NODES if n not in GATES]
    assert not missing, f"未注册的 stage1 节点：{missing}"


def test_spike_regime_signature() -> None:
    frame, ctx, r = _run("spike")
    assert r["3.1"].answer is True
    assert r["3.1"].branch == "spike"
    assert r["3.2"].answer is True
    assert r["3.5"].branch == "ongoing", "尖峰仍在新高途中，三路径应待回撤后确认"
    assert r["3.3"].answer is False, "§3.3 恒定：尖峰进行中不允许追"
    flags = judge_direction(frame).votes
    assert flags["ema"] + flags["gravity"] + flags["swing"] > 0


def test_micro_channel_signature() -> None:
    frame, ctx, r = _run("micro_channel")
    assert r["5.1"].answer is True
    assert r["5.1"].branch == "micro_channel"
    assert r["3.1"].answer is False, "低重叠但属连续通道推进，不是尖峰"


def test_tight_channel_signature() -> None:
    frame, ctx, r = _run("tight_channel")
    assert r["5.2"].answer is True
    assert r["5.2"].branch == "tight_channel"
    criteria = r["5.2"].evidence["criteria"]
    assert all(criteria.values()), f"紧凑通道三判据应同时成立：{criteria}"
    assert r["3.1"].answer is False
    assert r["3.1"].branch in ("pullback", "not_displaced", "channel")


def test_normal_channel_signature() -> None:
    frame, ctx, r = _run("normal_channel")
    pct = r["5.3"].evidence["pullback_pct"]
    assert 0.30 < pct <= 0.50, pct
    assert r["5.3"].answer is True
    assert r["5.2"].answer is False


def test_broad_channel_signature() -> None:
    frame, ctx, r = _run("broad_channel")
    assert r["5.4"].answer is True
    assert r["5.4"].branch == "broad_channel"
    assert r["5.4"].evidence["hh_hl_groups"] >= 3
    assert r["5.4"].evidence["overlap_mean"] >= 0.40


def test_trending_tr_signature() -> None:
    frame, ctx, r = _run("trending_tr")
    assert r["5.4"].branch == "trending_tr"
    assert r["5.4"].evidence["hh_hl_groups"] <= 2
    assert r["5.4"].answer is False, "trending_tr 不是宽通道"


def test_trading_range_signature() -> None:
    frame, ctx, r = _run("trading_range")
    assert r["6.3"].answer is True, "区间夹具收盘应落在边界三分位"
    assert r["6.3"].branch in ("lower_boundary", "upper_boundary")
    assert not any(r[n].answer is True for n in ("5.1", "5.2", "5.3", "5.4"))
    assert r["3.1"].answer is False
    assert r["3.1"].branch == "not_displaced", "区间内推进缺少突破位移，不应判尖峰"


def test_extreme_tr_signature() -> None:
    frame, ctx, r = _run("extreme_tr")
    assert r["6.4"].answer is True
    assert r["6.4"].branch == "barbwire"


def test_channel_pullback_ordering() -> None:
    values = {}
    for kind in ("tight_channel", "normal_channel", "broad_channel"):
        frame, ctx, r = _run(kind)
        values[kind] = r["5.2"].evidence["pullback_pct"]
    assert values["tight_channel"] < values["normal_channel"] < values["broad_channel"], values
    assert values["tight_channel"] <= 0.30
    assert 0.30 < values["normal_channel"] <= 0.50
    assert 0.50 < values["broad_channel"] <= 0.786


def test_channel_metrics_are_shared_and_cached() -> None:
    frame, ctx, _ = _run("normal_channel")
    first = _channel_metrics(frame, ctx)
    second = _channel_metrics(frame, ctx)
    assert first == second
    assert "sf" in ctx.extras, "结构特征应缓存在 GateContext.extras"


def test_prefill_direction_and_always_in_on_regimes() -> None:
    bullish = ("spike", "micro_channel", "tight_channel", "normal_channel", "broad_channel")
    for kind in bullish:
        frame = build_v2_frame(make_regime_rows(kind))
        direction = judge_direction(frame)
        assert direction.direction == "bullish", (kind, direction.reason)
        assert judge_always_in(frame).answer is True, kind


def test_trend_context_alignment() -> None:
    frame = build_v2_frame(make_regime_rows("tight_channel"))
    direction = judge_direction(frame)
    ctx = build_trend_context(frame, direction.direction)
    assert ctx["trading_direction"] == "bullish"
    assert ctx["relationship"] in ("aligned", "neutral_background", "mixed")
    assert ctx["conflict"] is (ctx["relationship"] == "conflict")


def test_insufficient_data_returns_safe_answers() -> None:
    frame = build_v2_frame(make_regime_rows("normal_channel", n=6))
    ctx = _ctx_for(frame)
    results = run_all(frame, ctx, nodes=STAGE1_NODES)
    assert len(results) == len(STAGE1_NODES)
    by_id = {r.node_id: r for r in results}
    assert by_id["3.1"].answer is False
    assert by_id["5.2"].answer is False
    for r in results:
        assert isinstance(r.answer, (bool, str))
        assert 0.0 <= r.confidence <= 1.0, r.node_id
        assert r.reason, r.node_id


def test_gates_are_deterministic() -> None:
    for kind in REGIMES:
        first = _run(kind)[2]
        second = _run(kind)[2]
        assert {
            n: (r.answer, r.branch, r.confidence) for n, r in first.items()
        } == {
            n: (r.answer, r.branch, r.confidence) for n, r in second.items()
        }, kind
