"""决策流水线：预填 → 阶段一闸门 → 周期定位 → 阶段二机械检查 → 灰区 → 合成。

代码优先原则：
- 清晰行情（周期明确、信号棒合格、风险通过）全程 0 次 LLM 调用；
- 只有灰区（关键节点边界/冲突/低置信）才组装一次精简 prompt；
- `gate_result=wait` 时沿用 v1 短路语义：阶段二直接合成「不下单」，不调 LLM。

节点顺序与 `二元决策.txt` 一致：§1 数据 → §2 预填 → §3/§5/§6 状态 →
§9 入场 → §10 风险 → §11 下单方式。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from pa_engine.config.thresholds import GrayZone as GZ
from pa_engine.config.thresholds import Guard, Prefill, Risk
from pa_engine.engine.gray_zone import (
    GrayZoneVerdict,
    Measurement,
    detect_conflicts,
    evaluate,
    measurements_from_results,
)
from pa_engine.engine.trace import split_traces
from pa_engine.features.structure import compute_simple_market_features, infer_price_tick
from pa_engine.gates.base import GateContext, GateResult, FrameLike, atr_of, run_all
from pa_engine.gates.order import LIMIT_ORDER, NO_ORDER, STOP_ORDER, route_from_context
from pa_engine.gates.risk import TradePlan, prepare_plan
from pa_engine.prefill import build_trend_context, judge_always_in, judge_direction

STAGE1_GATES = (
    "3.1", "3.2", "3.3", "3.4", "3.5", "3.6", "3.7",
    "4.1", "4.2",
    "5.1", "5.2", "5.3", "5.4", "5.5",
    "6.1", "6.2", "6.3", "6.4", "6.5", "6.6",
    "7.1", "7.2", "7.3", "7.4",
    "8.1", "8.2", "8.3",
)
SIGNAL_ENTRY_GATES = ("9.2", "9.3", "9.5", "9.7")
CONTEXT_ENTRY_GATES = ("9.1", "9.4", "9.6")
RISK_GATES = ("10.1", "10.2", "10.3")
ORDER_GATES = ("11.1", "11.2", "11.3", "11.4")

# v1 语义对齐（PA_Agent/pa_agent/ai/decision_tree.py:611-655）：
# 仅 §10.3 交易者方程否决与 §14 禁令触发允许 reject；
# §10.1/§10.2 失败 = 无有效止损方案，无方案可拒绝，终态必须是 wait。
_REJECT_NODES = ("14", "10.3")


def _terminal_outcome_for(node_id: str) -> str:
    return "reject" if node_id in _REJECT_NODES else "wait"

CHANNELS = ("micro_channel", "tight_channel", "normal_channel", "broad_channel")
RANGE_STATES = ("trading_range", "trending_tr", "extreme_tr")


class GrayZoneLLM(Protocol):
    """感知层最小契约（ai/client.py 实现）。"""

    def complete(self, prompt: str) -> dict[str, Any]: ...


@dataclass
class DecisionOutcome:
    decision: dict[str, Any]
    stage1: dict[str, Any] = field(default_factory=dict)
    gate_results: list[GateResult] = field(default_factory=list)
    gray_zone: GrayZoneVerdict | None = None
    llm_called: bool = False
    llm_answer: dict[str, Any] | None = None
    prompt_stats: dict[str, Any] = field(default_factory=dict)


# ── 阶段一 ───────────────────────────────────────────────────────────────────


def _prefill_results(frame: FrameLike, ctx: GateContext) -> tuple[list[GateResult], dict[str, Any]]:
    direction = judge_direction(frame)
    always_in = judge_always_in(frame)
    context = build_trend_context(frame, direction.direction)

    ctx.extras["direction"] = direction.direction
    ctx.extras["always_in"] = always_in.always_in
    ctx.extras["twenty_gb"] = always_in.twenty_gb
    ctx.extras["twenty_gb_side"] = always_in.twenty_gb_side
    ctx.extras["twenty_gb_first_touch"] = always_in.twenty_gb_first_touch
    ctx.extras["relationship"] = context["relationship"]
    ctx.extras["background_direction"] = context["background_direction"]

    direction_conf = 0.8
    if direction.direction == "neutral":
        direction_conf = 0.6
    elif abs(direction.score) >= Prefill.DIRECTION_STRONG_SHORT_SCORE:
        direction_conf = 0.9
    ai_conf = 0.85 if always_in.answer else 0.8
    results = [
        GateResult(
            node_id="2.3",
            answer=direction.direction,
            branch=direction.direction,
            confidence=direction_conf,
            evidence={"score": direction.score, "votes": direction.votes},
            reason=direction.reason,
        ),
        GateResult(
            node_id="2.4",
            answer=always_in.answer,
            branch=always_in.branch or "none",
            confidence=ai_conf,
            evidence={
                "always_in": always_in.always_in,
                "near_ratio": round(always_in.near_ratio, 4),
                "bg_ratio": round(always_in.bg_ratio, 4),
                "strength": always_in.strength,
                "twenty_gb": always_in.twenty_gb,
            },
            reason=always_in.reason,
        ),
        GateResult(
            node_id="2.2",
            answer=context["relationship"],
            branch=context["relationship"],
            confidence=0.6 if context["conflict"] else 0.8,
            evidence={
                "background_direction": context["background_direction"],
                "trading_direction": context["trading_direction"],
                "recent_spike": context["recent_spike"],
                "conflict": context["conflict"],
            },
            reason=(
                f"背景{context['background_direction']} / 近期{context['trading_direction']}"
                f" → {context['relationship']}"
            ),
        ),
        GateResult(
            node_id="2.1",
            answer=direction.direction != "neutral",
            branch=direction.direction,
            confidence=0.8 if direction.direction != "neutral" else 0.6,
            evidence={"score": direction.score},
            reason=f"五信号投票 score={direction.score}",
        ),
        GateResult(
            node_id="2.5",
            answer=bool(always_in.answer and context["recent_spike"]),
            branch=context["recent_spike"] or "none",
            confidence=0.75,
            evidence={"always_in": always_in.always_in, "recent_spike": context["recent_spike"]},
            reason=(
                "Always In 且近端存在尖峰，惯性足以趋势跟踪"
                if always_in.answer and context["recent_spike"]
                else "惯性不足，只做高确定性回撤/边界"
            ),
        ),
    ]
    stage1_meta = {
        "direction": direction.direction,
        "direction_score": direction.score,
        "always_in": always_in.always_in,
        "always_in_branch": always_in.branch,
        "trend_context": context,
    }
    return results, stage1_meta


def classify_cycle(results: list[GateResult]) -> str:
    """按判定树顺序定位周期（与 8 状态判定顺序一致）。"""
    by_node = {r.node_id: r for r in results}
    if by_node.get("3.1") and by_node["3.1"].answer is True:
        return "spike"
    if by_node.get("5.1") and by_node["5.1"].answer is True:
        return "micro_channel"
    if by_node.get("5.2") and by_node["5.2"].answer is True:
        return "tight_channel"
    if by_node.get("5.3") and by_node["5.3"].answer is True:
        return "normal_channel"
    if by_node.get("5.4") and by_node["5.4"].branch == "broad_channel":
        return "broad_channel"
    if by_node.get("6.4") and by_node["6.4"].answer is True:
        return "extreme_tr"
    if by_node.get("5.4") and by_node["5.4"].branch == "trending_tr":
        return "trending_tr"
    if by_node.get("6.3") and by_node["6.3"].evidence.get("price_position") is not None:
        return "trading_range"
    return "unknown"


def _risk_blocker(results: list[GateResult]) -> GateResult | None:
    """§10 的答案极性不同：10.2「是」= 止损过大（失败），10.1/10.3「是」= 通过。"""
    by_node = {r.node_id: r for r in results}
    for node in ("10.1", "10.2", "10.3"):
        result = by_node.get(node)
        if result is None:
            continue
        failed = result.answer is True if node == "10.2" else result.answer is not True
        if failed:
            return result
    return None


def _entry_blocker(results: list[GateResult], entry_style: str = "signal") -> GateResult | None:
    """返回第一个阻断入场的 §9 节点（按入场路径选择检查项）。"""
    by_node = {r.node_id: r for r in results}
    if entry_style == "background_limit":
        nodes = ("9.0P",)
    else:
        nodes = ("9.0", "9.2", "9.3", "9.5")
    for node in nodes:
        result = by_node.get(node)
        if result is None:
            continue
        if node == "9.3":
            if result.answer is True:
                return result
        elif result.answer is not True:
            return result
    return None


# ── 阶段二：方案合成 ─────────────────────────────────────────────────────────


def build_trade_plan(
    frame: FrameLike,
    ctx: GateContext,
    *,
    cycle_position: str,
    direction: str,
    order_type: str,
    structure=None,
    entry_style: str = "signal",
    background_limit: dict[str, Any] | None = None,
) -> TradePlan | None:
    """由信号棒或背景结构位合成三价与止损（代码路径）。

    信号棒路径：
    - 信号棒 = K2；突破单入场 = K2 极点 ±1 跳；限价单 = K2 回撤极点 ±1 跳；
      区间限价 = 区间边界 ±1 跳；
    - 止损 = min(信号棒极点, 入场) 外 1 跳（做多），保证 §10.1；
    - TP1 = 入场方向上最近的结构阻力/支撑；TP2 = Measured Move 目标。

    背景限价路径（§9.0P）：
    - 入场 = 结构位/边界/EMA 候选；止损 = 结构失效位外 1 跳；
    - TP1 = 最近结构目标；TP2 = MM/对边；止损依据记为 structure。
    """
    bars = getattr(frame, "bars", ()) or ()
    if len(bars) < 2 or structure is None or direction not in ("bullish", "bearish"):
        return None
    bull = direction == "bullish"
    signal = bars[1]
    tick = infer_price_tick(frame) or 0.01
    atr = atr_of(frame, ctx) or 0.0

    if entry_style == "background_limit" and background_limit:
        entry = float(background_limit["entry"])
        invalidation = float(background_limit["stop"])
        target = float(background_limit["target"])
        stop = invalidation - tick if bull else invalidation + tick
        risk = abs(entry - stop)
        if risk <= 0:
            return None
        if bull:
            if not stop < entry < target:
                return None
            mm = sorted(
                m.target_price for m in structure.measured_moves if m.target_price > target + tick
            )
            tp1 = target
            tp2 = mm[0] if mm else tp1 + max(tp1 - entry, risk)
            valid = stop < entry < tp1 < tp2
        else:
            if not target < entry < stop:
                return None
            mm = sorted(
                (m.target_price for m in structure.measured_moves if m.target_price < target - tick),
                reverse=True,
            )
            tp1 = target
            tp2 = mm[0] if mm else tp1 - max(entry - tp1, risk)
            valid = tp2 < tp1 < entry < stop
        if not valid:
            return None
        return TradePlan(
            direction=direction,
            entry=round(entry, 10),
            stop=round(stop, 10),
            tp1=round(tp1, 10),
            tp2=round(tp2, 10),
            estimated_win_rate=Risk.WIN_RATE_PRIOR.get(cycle_position, 0.5) * 100.0,
            channel_width=(
                None
                if cycle_position in RANGE_STATES or structure.range_high is None
                else float(structure.range_high) - float(structure.range_low)
            ),
            range_width=(
                float(structure.range_high) - float(structure.range_low)
                if cycle_position in RANGE_STATES
                else None
            ),
            atr=atr or None,
            spike_height=None,
            tick=tick,
            signal_bar_extreme=None,
            signal_bar_seq=None,
            stop_reference="structure",
            entry_style="background_limit",
            structure_stop_level=round(invalidation, 10),
        )
    if order_type == STOP_ORDER:
        entry = float(signal.high) + tick if bull else float(signal.low) - tick
    elif cycle_position == "trading_range" and structure.range_low is not None:
        entry = (
            float(structure.range_low) + tick
            if bull
            else float(structure.range_high) - tick
        )
    else:
        entry = float(signal.low) + tick if bull else float(signal.high) - tick

    stop = (
        min(float(signal.low), entry) - tick
        if bull
        else max(float(signal.high), entry) + tick
    )
    risk = abs(entry - stop)
    if risk <= 0:
        return None

    if bull:
        candidates = sorted(p for p in structure.resistances if p > entry + tick)
        tp1 = candidates[0] if candidates else None
        if tp1 is None:
            target = structure.range_high
            tp1 = target if target and target > entry + tick else entry + 2 * risk
        mm = sorted(
            m.target_price for m in structure.measured_moves if m.target_price > tp1 + tick
        )
        tp2 = mm[0] if mm else tp1 + max(tp1 - entry, risk)
        valid = stop < entry < tp1 < tp2
    else:
        candidates = sorted((p for p in structure.supports if p < entry - tick), reverse=True)
        tp1 = candidates[0] if candidates else None
        if tp1 is None:
            target = structure.range_low
            tp1 = target if target and target < entry - tick else entry - 2 * risk
        mm = sorted(
            (m.target_price for m in structure.measured_moves if m.target_price < tp1 - tick),
            reverse=True,
        )
        tp2 = mm[0] if mm else tp1 - max(entry - tp1, risk)
        valid = tp2 < tp1 < entry < stop
    if not valid:
        return None

    spike_height = None
    if cycle_position == "spike":
        assessment = ctx.extras.get("spike_assessment")
        run = getattr(assessment, "run", None)
        spike_height = float(run.height) if run is not None and run.height > 0 else None

    return TradePlan(
        direction=direction,
        entry=round(entry, 10),
        stop=round(stop, 10),
        tp1=round(tp1, 10),
        tp2=round(tp2, 10),
        estimated_win_rate=Risk.WIN_RATE_PRIOR.get(cycle_position, 0.5) * 100.0,
        channel_width=(
            None
            if cycle_position in RANGE_STATES or structure.range_high is None
            else float(structure.range_high) - float(structure.range_low)
        ),
        range_width=(
            float(structure.range_high) - float(structure.range_low)
            if cycle_position in RANGE_STATES
            else None
        ),
        atr=atr or None,
        spike_height=spike_height,
        tick=tick,
        signal_bar_extreme=float(signal.low) if bull else float(signal.high),
        signal_bar_seq=int(signal.seq),
        stop_reference="signal_bar",
        entry_style="signal",
    )


# ── 合成输出 ─────────────────────────────────────────────────────────────────


def _base_decision(
    *,
    cycle_position: str,
    direction: str,
    always_in: str,
    gate_result: str,
    results: list[GateResult],
    reason: str,
    entry_style: str = "none",
    patterns: list[str] | None = None,
    reversal: dict[str, Any] | None = None,
    wedge: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stage1_trace, stage2_trace = split_traces(results)
    return {
        "cycle_position": cycle_position,
        "direction": direction,
        "always_in": always_in,
        "gate_result": gate_result,
        "gate_trace": stage1_trace,
        "decision_trace": stage2_trace,
        "order_type": NO_ORDER,
        "order_direction": None,
        "entry_price": None,
        "stop_loss_price": None,
        "take_profit_price": None,
        "take_profit_price_2": None,
        "estimated_win_rate": None,
        "estimated_win_rate_reasoning": "",
        "entry_basis_bar": None,
        "entry_basis_extreme": None,
        "entry_rule": None,
        "entry_style": entry_style,
        "patterns": patterns or [],
        "reversal": reversal or {},
        "wedge": wedge or {},
        "reason": reason,
        "terminal": {"node_id": "", "outcome": "wait", "label": reason},
    }


def _revoke_plan(
    decision: dict[str, Any], *, node_id: str, label: str, outcome: str = "wait"
) -> None:
    """撤销已应用的方案（LLM 复核或 §14 守卫触发）。"""
    decision["order_type"] = NO_ORDER
    decision["order_direction"] = None
    for key in (
        "entry_price",
        "stop_loss_price",
        "take_profit_price",
        "take_profit_price_2",
        "estimated_win_rate",
        "entry_basis_bar",
        "entry_basis_extreme",
        "entry_rule",
    ):
        decision[key] = None
    decision["terminal"] = {"node_id": node_id, "outcome": outcome, "label": label}
    decision["reason"] = label


def _override_win_rate(
    frame: FrameLike,
    ctx: GateContext,
    plan: TradePlan,
    win_rate: float,
    results: list[GateResult],
) -> tuple[TradePlan, GateResult | None]:
    """用感知层胜率重算 §10.2/§10.3，并替换结果集中旧条目。"""
    updated = replace(plan, estimated_win_rate=float(win_rate))
    ctx.extras["trade_plan"] = updated
    fresh = run_all(frame, ctx, nodes=("10.2", "10.3"))
    fresh_ids = {r.node_id for r in fresh}
    results[:] = [r for r in results if r.node_id not in fresh_ids] + fresh
    blocker: GateResult | None = None
    for result in fresh:
        if result.node_id == "10.2" and result.answer is True:
            blocker = result
        elif result.node_id == "10.3" and result.answer is not True:
            blocker = result
    return updated, blocker


def _apply_plan(decision: dict[str, Any], plan: TradePlan, ctx: GateContext) -> None:
    bull = plan.direction == "bullish"
    decision.update(
        {
            "order_direction": "做多" if bull else "做空",
            "entry_price": plan.entry,
            "stop_loss_price": plan.stop,
            "take_profit_price": plan.tp1,
            "take_profit_price_2": plan.tp2,
            "estimated_win_rate": plan.estimated_win_rate,
            "estimated_win_rate_reasoning": "代码路径使用状态胜率先验（灰区由 LLM 覆盖）",
            "entry_basis_bar": f"K{plan.signal_bar_seq}" if plan.signal_bar_seq else None,
            "entry_basis_extreme": ("low" if bull else "high") if plan.signal_bar_seq else None,
            "entry_rule": (
                f"K{plan.signal_bar_seq}{'低点' if bull else '高点'}外1跳"
                if plan.entry_style == "signal"
                else f"结构位限价（{ctx.extras.get('background_limit', {}).get('sources')}）"
            ),
            "entry_style": plan.entry_style,
            "stop_reference": plan.stop_reference,
            "stop_widen": ctx.extras.get("stop_widen", {}),
        }
    )


# ── 主流程 ───────────────────────────────────────────────────────────────────


def _anchor_dicts(anchor: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """锚点对象 → (prompt 块, 记录块)；兼容 AnchorContext 与裸 dict。"""
    if anchor is None:
        return None, None
    if hasattr(anchor, "as_prompt") and hasattr(anchor, "as_record"):
        return anchor.as_prompt(), anchor.as_record()
    if isinstance(anchor, dict):
        prompt = anchor.get("prompt") if "prompt" in anchor else anchor
        record = anchor.get("record") if "record" in anchor else anchor
        return prompt if isinstance(prompt, dict) else None, (
            record if isinstance(record, dict) else None
        )
    return None, None


def decide(
    frame: FrameLike,
    ctx: GateContext | None = None,
    *,
    llm: GrayZoneLLM | None = None,
    measurements: list[Measurement] | tuple[Measurement, ...] | None = None,
    band: float = GZ.BAND,
    vote: int = 0,
    anchor: Any | None = None,
) -> DecisionOutcome:
    """执行一次完整决策。

    - `vote`>1 时对灰区 prompt 做自一致性投票（T3.3），默认 0＝单次调用；
    - `anchor`（`records.history.AnchorContext`）只进灰区 prompt 与记录，
      绝不影响周期/方向/闸门/价格；无灰区时仍然零调用。
    """
    ctx = ctx or GateContext()
    anchor_prompt, anchor_record = _anchor_dicts(anchor)
    if anchor_record is not None:
        ctx.extras["anchor"] = anchor_record
    bars = getattr(frame, "bars", ()) or ()
    results: list[GateResult] = []
    meta: dict[str, Any] = {
        "direction": "neutral",
        "direction_score": 0,
        "always_in": "none",
        "always_in_branch": None,
        "trend_context": {},
    }

    if bars:
        prefill, meta = _prefill_results(frame, ctx)
        results.extend(prefill)

    if len(bars) < Prefill.BAR_COUNT_MIN:
        results.append(
            GateResult(
                "1.1",
                False,
                "insufficient",
                1.0,
                {"bars": len(bars), "required": Prefill.BAR_COUNT_MIN},
                f"K线不足（{len(bars)}<{Prefill.BAR_COUNT_MIN}），不臆造判断",
            )
        )
        decision = _base_decision(
            cycle_position="unknown",
            direction=meta["direction"],
            always_in=meta["always_in"],
            gate_result="unknown",
            results=results,
            reason=f"数据不足：{len(bars)} 根 < {Prefill.BAR_COUNT_MIN} 根，等待更多已收盘K线",
        )
        decision["terminal"] = {"node_id": "1.1", "outcome": "unknown", "label": "数据不足"}
        return DecisionOutcome(
            decision=decision,
            stage1=meta,
            gate_results=results,
            gray_zone=GrayZoneVerdict(False, (), ("数据不足，不触发感知层",)),
        )

    # 阶段一：状态闸门
    stage1_results = run_all(frame, ctx, nodes=STAGE1_GATES)
    results.extend(stage1_results)
    cycle = classify_cycle(stage1_results)
    ctx.extras["cycle_position"] = cycle

    by_node = {r.node_id: r for r in results}
    spike_stage = "none"
    if by_node["3.1"].answer is True:
        spike_stage = "ongoing" if by_node["3.2"].answer is True else "ended"
    spike_path = by_node["3.5"].branch if by_node["3.1"].answer is True else "none"
    ctx.extras["spike_stage"] = spike_stage
    ctx.extras["spike_path"] = spike_path
    ctx.extras["zone"] = by_node["6.3"].branch
    ctx.extras["breakout_quality"] = by_node["6.5"].branch

    gate_result = "wait" if cycle == "extreme_tr" else ("unknown" if cycle == "unknown" else "proceed")
    results.extend(
        [
            GateResult("1.2", cycle != "unknown", cycle, 0.85 if cycle != "unknown" else 0.5,
                       {"cycle_position": cycle}, f"周期定位={cycle}"),
            GateResult("1.3", cycle == "extreme_tr", "extreme_tr" if cycle == "extreme_tr" else "normal",
                       0.85, {"cycle_position": cycle},
                       "极端混乱区间，禁止交易" if cycle == "extreme_tr" else "非极端混乱"),
        ]
    )

    structure = compute_simple_market_features(frame)

    # 结构翻转冷却（v1 校验器行为，two_stage.py structure_flip_cooldown_bars）：
    # 仅当 --incremental 锚定存在且 cooldown>0 时生效——本轮方向与上次记录相异
    # 且新增K线数未达冷却窗口 → §14 追加 structure_flip_cooldown 禁令（终态 wait）。
    # 默认 Guard.STRUCTURE_FLIP_COOLDOWN_BARS=0 完全禁用，单次决策行为不变。
    cooldown = Guard.STRUCTURE_FLIP_COOLDOWN_BARS
    if cooldown > 0 and anchor_record is not None:
        prev_info = anchor_record.get("previous") or {}
        prev_direction = prev_info.get("direction")
        new_bars = int(anchor_record.get("new_bars") or 0)
        if (
            prev_direction in ("bullish", "bearish")
            and meta.get("direction") in ("bullish", "bearish")
            and prev_direction != meta.get("direction")
            and new_bars < cooldown
        ):
            ctx.extras["structure_flip_blocked"] = True
            ctx.extras["structure_flip_info"] = {
                "previous_direction": prev_direction,
                "current_direction": meta.get("direction"),
                "new_bars": new_bars,
                "cooldown": cooldown,
            }

    patterns = list(ctx.extras.get("patterns") or [])
    reversal_a = ctx.extras.get("reversal_assessment")
    wedge_a = ctx.extras.get("wedge_assessment")
    reversal_info = (
        {
            "trend": reversal_a.trend,
            "confirmed": reversal_a.confirmed,
            "trendline_broken": reversal_a.trendline_broken,
            "failed_extreme_test": reversal_a.failed_extreme_test,
            "single_bar": reversal_a.single_bar,
        }
        if reversal_a is not None
        else {}
    )
    wedge_info = (
        {
            "three_push": wedge_a.three_push,
            "direction": wedge_a.wedge_direction,
            "type": str(ctx.extras.get("wedge_type") or "none"),
            "parabolic": bool(ctx.extras.get("parabolic")),
        }
        if wedge_a is not None
        else {}
    )

    plan: TradePlan | None = None
    blocker: GateResult | None = None
    route = route_from_context(ctx)
    entry_style = "none"

    if gate_result == "proceed":
        # §9.0 与 §9.0P 是并列的合法性检查：信号棒合格走信号路径，
        # 不合格时必须评估背景驱动限价，两条都失败才终止（v1 §9.0 原文）。
        signal_gate = run_all(frame, ctx, nodes=["9.0"])[0]
        results.append(signal_gate)
        if signal_gate.answer is True:
            entry_style = "signal"
            ctx.extras["entry_style"] = "signal"
            results.extend(run_all(frame, ctx, nodes=SIGNAL_ENTRY_GATES + CONTEXT_ENTRY_GATES))
            blocker = _entry_blocker(results, "signal")
        else:
            limit_gate = run_all(frame, ctx, nodes=["9.0P"])[0]
            results.append(limit_gate)
            if limit_gate.answer is True:
                entry_style = "background_limit"
                ctx.extras["entry_style"] = "background_limit"
                results.extend(run_all(frame, ctx, nodes=CONTEXT_ENTRY_GATES + ("9.7",)))
                blocker = _entry_blocker(results, "background_limit")
            else:
                entry_style = "none"
                blocker = limit_gate

        route = route_from_context(ctx)
        if blocker is None and route.order_type != NO_ORDER:
            plan = build_trade_plan(
                frame,
                ctx,
                cycle_position=cycle,
                direction=meta["direction"],
                order_type=route.order_type,
                structure=structure,
                entry_style=entry_style,
                background_limit=ctx.extras.get("background_limit"),
            )
            if plan is not None:
                plan = prepare_plan(plan, ctx)
                ctx.extras["stop_reference"] = plan.stop_reference
                results.extend(run_all(frame, ctx, nodes=RISK_GATES))
        order_results = run_all(frame, ctx, nodes=ORDER_GATES)
        results.extend(order_results)
        if route.order_type == NO_ORDER:
            blocker = next((r for r in order_results if r.answer is True), None)
        # §14 最终守卫：仅对将要下单的方案做硬禁令检查
        if plan is not None and blocker is None:
            guard = run_all(frame, ctx, nodes=["14"])[0]
            results.append(guard)
            if guard.answer is True:
                blocker = guard

    decision = _base_decision(
        cycle_position=cycle,
        direction=meta["direction"],
        always_in=meta["always_in"],
        gate_result=gate_result,
        results=results,
        reason="",
        entry_style=entry_style,
        patterns=patterns,
        reversal=reversal_info,
        wedge=wedge_info,
    )
    decision.update(
        {
            "spike_stage": spike_stage,
            "spike_path": spike_path,
            "zone": ctx.extras["zone"],
            "breakout_quality": ctx.extras["breakout_quality"],
            "direction_score": meta.get("direction_score"),
        }
    )
    if anchor_record is not None:
        decision["incremental"] = anchor_record

    trade_ok = True
    terminal_node = ""
    terminal_outcome = "wait"
    reason = ""
    if gate_result != "proceed":
        terminal_node = "1.3" if cycle == "extreme_tr" else "1.2"
        reason = (
            "极端混乱区间（extreme_tr），阶段二直接合成不下单"
            if cycle == "extreme_tr"
            else "无法识别市场周期，等待结构清晰"
        )
        trade_ok = False
    elif blocker is not None:
        terminal_node = blocker.node_id
        terminal_outcome = _terminal_outcome_for(blocker.node_id)
        reason = f"§{blocker.node_id} 阻断：{blocker.reason}"
        trade_ok = False
    elif plan is None:
        terminal_node = route.node_id or ("11.1" if route.order_type == NO_ORDER else "9.0")
        reason = f"无有效下单方案：{route.reason}"
        trade_ok = False
    else:
        risk_fail = _risk_blocker(results)
        if risk_fail is not None:
            terminal_node = risk_fail.node_id
            terminal_outcome = _terminal_outcome_for(risk_fail.node_id)
            reason = f"§{risk_fail.node_id} 未通过：{risk_fail.reason}"
            trade_ok = False
        else:
            terminal_node = route.node_id or "11.4"
            terminal_outcome = "trade"
            reason = f"{route.order_type}：{route.reason}"

    if trade_ok and plan is not None:
        _apply_plan(decision, plan, ctx)
        decision["order_type"] = route.order_type
        decision["reason"] = reason
        decision["terminal"] = {
            "node_id": terminal_node,
            "outcome": terminal_outcome,
            "label": f"{route.order_type}（{route.basis}）",
        }
    else:
        decision["reason"] = reason
        decision["terminal"] = {
            "node_id": terminal_node,
            "outcome": terminal_outcome,
            "label": reason,
        }

    # 灰区检测：基础关键节点 + 当前路径相关的动态关键节点
    relationship = ctx.extras.get("relationship", "neutral")
    if measurements is None:
        measurements = measurements_from_results(results, cycle_position=cycle)
    conflicts = detect_conflicts(
        results, direction=meta["direction"], relationship=relationship
    )
    # v1 §2.3：强短窗方向（|score|≥4）优先于旧背景，不倒扣、不触发灰区。
    if abs(int(meta.get("direction_score") or 0)) >= Prefill.DIRECTION_STRONG_SHORT_SCORE:
        conflicts = [item for item in conflicts if item[0] != "direction_vs_background"]
    critical = ["2.4", "3.1"]
    if cycle in CHANNELS or trade_ok:
        critical.extend(["5.2", "5.4"])
    if cycle in RANGE_STATES:
        critical.append("6.5")
    if trade_ok and entry_style == "signal":
        critical.append("9.0")
    if trade_ok and entry_style == "background_limit":
        critical.append("9.0P")
    if entry_style != "none" and reversal_info.get("confirmed"):
        critical.append("7.2")
    if entry_style != "none" and wedge_info.get("three_push"):
        critical.extend(["8.2", "8.3"])
    gray = evaluate(
        results,
        measurements=measurements,
        band=band,
        critical_nodes=tuple(dict.fromkeys(critical)),
        conflicts=conflicts,
    )

    llm_answer: dict[str, Any] | None = None
    prompt_stats: dict[str, Any] = {"bytes": 0, "chars": 0, "loaded": []}
    # gate_result != proceed 时沿用 v1 短路语义：不进入感知层
    llm_allowed = llm is not None and gray.need_llm and gate_result == "proceed"
    llm_called = False
    if llm_allowed:
        from pa_engine.ai.prompt import build_gray_zone_prompt

        prompt = build_gray_zone_prompt(
            frame, results, gray, cycle_position=cycle, anchor=anchor_prompt
        )
        encoded = prompt.encode("utf-8")
        loaded = ["persona", "evidence", "K10-K1"]
        if anchor_prompt is not None and "## 上次结论" in prompt:
            loaded.append("anchor")
        prompt_stats = {
            "bytes": len(encoded),
            "chars": len(prompt),
            "estimated_tokens": round(len(prompt) / 1.6, 1),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "loaded": loaded,
        }
        try:
            if vote and int(vote) >= 2:
                from pa_engine.engine.consistency import vote_gray_zone

                ballot = vote_gray_zone(llm, prompt, samples=int(vote))
                decision["vote"] = ballot.as_record()
                if not ballot.valid:
                    raise RuntimeError(
                        "自一致性投票无有效答复：" + "; ".join(ballot.errors[:3])
                    )
                llm_answer = ballot.answer
            else:
                llm_answer = _normalize_llm_reply(llm.complete(prompt))
        except Exception as exc:  # 感知层失败不得中断链路
            decision["llm_error"] = f"{type(exc).__name__}: {exc}"
        else:
            llm_called = True
            decision["llm_review"] = llm_answer

            # 只让 LLM 覆盖感知字段；周期/方向仍以代码为准（方向是程序节点 §2.3）。
            # 信号棒质量只否决信号棒路径；计划型限价本来就不要求信号棒。
            # 「看不懂」出口（v1 §0.1/§0.2 对齐）：感知层判 confused → 撤销方案等 wait，
            # 不推翻代码周期与方向（对任何入场路径生效）。
            if (
                trade_ok
                and plan is not None
                and str(llm_answer.get("market_legibility") or "unknown") == "confused"
            ):
                _revoke_plan(
                    decision,
                    node_id="0.1",
                    label="LLM复核：当前市场可读性不足（看不懂），撤销方案等待",
                )
                trade_ok = False

            if (
                trade_ok
                and plan is not None
                and entry_style == "signal"
                and llm_answer.get("signal_bar_quality") == "fail"
            ):
                _revoke_plan(
                    decision,
                    node_id="9.0",
                    label="LLM复核：信号棒质量不足，撤销方案",
                )
                trade_ok = False

            # 胜率覆盖：LLM 的 estimated_win_rate 只影响交易者方程，不改变价格几何
            if (
                trade_ok
                and plan is not None
                and float(llm_answer.get("estimated_win_rate") or 0) > 0
            ):
                updated_plan, risk_block = _override_win_rate(
                    frame, ctx, plan, float(llm_answer["estimated_win_rate"]), results
                )
                if risk_block is not None:
                    _revoke_plan(
                        decision,
                        node_id=risk_block.node_id,
                        label=f"LLM胜率覆盖后 §{risk_block.node_id} 未通过，撤销方案",
                        outcome="reject",
                    )
                    trade_ok = False
                else:
                    plan = updated_plan
                    decision["estimated_win_rate"] = plan.estimated_win_rate
                    decision["estimated_win_rate_reasoning"] = str(
                        llm_answer.get("win_rate_reason") or "感知层胜率判断"
                    )

            # 反转/楔形感知否决：只加禁令，不改写代码周期
            if trade_ok and plan is not None:
                llm_wedge = str(llm_answer.get("wedge_type") or "none")
                always_in_now = str(ctx.extras.get("always_in") or "none")
                reversal_ban = (
                    bool(llm_answer.get("reversal_attempt"))
                    and always_in_now in ("bullish", "bearish")
                    and str(meta.get("direction")) != always_in_now
                )
                if llm_wedge in ("reversal", "parabolic"):
                    _revoke_plan(
                        decision,
                        node_id="8.2" if llm_wedge == "reversal" else "8.3",
                        label=f"感知层识别{('楔形反转' if llm_wedge == 'reversal' else '抛物线楔形')}，禁止追单",
                    )
                    trade_ok = False
                elif reversal_ban:
                    _revoke_plan(
                        decision,
                        node_id="7.2",
                        label="感知层确认反转尝试且方向逆背景，禁止逆势下单",
                    )
                    trade_ok = False

    decision["gray_zone"] = {
        "need_llm": gray.need_llm,
        "contested": list(gray.contested_nodes),
        "reasons": list(gray.reasons),
        "llm_called": llm_called,
    }
    decision["prompt_stats"] = prompt_stats
    decision["diagnosis_confidence"] = _diagnosis_confidence(results, cycle)

    meta.update(
        {
            "cycle_position": cycle,
            "gate_result": gate_result,
            "spike_stage": spike_stage,
            "spike_path": spike_path,
            "zone": ctx.extras["zone"],
            "breakout_quality": ctx.extras["breakout_quality"],
            "patterns": patterns,
            "entry_style": entry_style,
            "reversal": reversal_info,
            "wedge": wedge_info,
            "diagnosis_confidence": decision["diagnosis_confidence"],
        }
    )
    return DecisionOutcome(
        decision=decision,
        stage1=meta,
        gate_results=results,
        gray_zone=gray,
        llm_called=llm_called,
        llm_answer=llm_answer,
        prompt_stats=prompt_stats,
    )


def _normalize_llm_reply(reply: Any) -> dict[str, Any]:
    from pa_engine.ai.contract import parse_payload

    if isinstance(reply, str):
        try:
            reply = json.loads(reply)
        except json.JSONDecodeError:
            return {}
    if not isinstance(reply, dict):
        return {}
    answer = parse_payload(reply)
    if answer is None:
        return {}
    return {
        "cycle_position": answer.cycle_position,
        "direction": answer.direction,
        "always_in": answer.always_in,
        "signal_bar_quality": answer.signal_bar_quality,
        "qualitative_notes": answer.qualitative_notes,
        "confidence": answer.confidence,
        "estimated_win_rate": answer.estimated_win_rate,
        "win_rate_reason": answer.win_rate_reason,
        "reversal_attempt": answer.reversal_attempt,
        "wedge_type": answer.wedge_type,
        "entry_quality": answer.entry_quality,
        "second_entry": answer.second_entry,
        "market_legibility": answer.market_legibility,
    }


_CYCLE_NODE = {
    "spike": "3.1",
    "micro_channel": "5.1",
    "tight_channel": "5.2",
    "normal_channel": "5.3",
    "broad_channel": "5.4",
    "trending_tr": "5.4",
    "extreme_tr": "6.4",
    "trading_range": "6.3",
}


def _diagnosis_confidence(results: list[GateResult], cycle: str) -> float:
    by_node = {r.node_id: r for r in results}
    key_nodes = ["2.3", "2.4", _CYCLE_NODE.get(cycle, "")]
    confidences = [by_node[n].confidence for n in key_nodes if n and n in by_node]
    if not confidences:
        return 0.0
    return round(100.0 * min(confidences), 1)
