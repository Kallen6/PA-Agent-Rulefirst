"""风险收益闸门（§10，纯算术，最高确定性）。

节点映射（二元决策.txt §10）：
    §10.1 是否能明确止损   止损 = 信号棒极点外 1 跳（唯一标准）
    §10.2 止损是否过大     四条件任一成立即过大：
                           RR<档位下限 / >通道宽40% / >区间宽10% / >2×ATR
    §10.3 交易者方程       胜率%×回报 > (100−胜率)%×风险（仅用 TP1）

执行顺序（与 v1 一致）：
    合成方案 → `prepare_plan()` 按档位 `target_rr` 外扩止损（不超过尖峰高度 50%）
    → §10.1/§10.2/§10.3 全部基于外扩后的方案判定。

档位（`engine/stance.py`）：仅影响最低 RR 与方程余量，不改变方向/周期/入场路径。
未注入档位时回落到 `Stance.DEFAULT`（均衡），保证纯代码路径与 v1 回放可用。

输入来自 `GateContext.extras["trade_plan"]`（由流水线合成），闸门本身不做 IO、不调 LLM。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from pa_engine.config.thresholds import Risk
from pa_engine.gates.base import GateContext, GateResult, FrameLike, register

if TYPE_CHECKING:  # 仅类型标注；运行时延迟导入避免 engine ↔ gates 循环导入
    from pa_engine.engine.stance import StancePolicy


def _policy(ctx: GateContext) -> "StancePolicy":
    """读取当前档位策略（延迟导入，规避包级循环依赖）。"""
    from pa_engine.engine.stance import stance_of

    return stance_of(ctx)


@dataclass(frozen=True)
class TradePlan:
    """待校验的下单方案（价格单位与 K 线一致）。"""

    direction: str                      # "bullish" / "bearish"
    entry: float
    stop: float
    tp1: float
    tp2: float | None = None
    estimated_win_rate: float = 0.0     # 0–100（百分数）
    channel_width: float | None = None
    range_width: float | None = None
    atr: float | None = None
    spike_height: float | None = None
    tick: float = 0.01
    signal_bar_extreme: float | None = None
    signal_bar_seq: int | None = None
    widened: bool = False
    # §9.0P 结构止损路径：止损依据结构失效位而非信号棒极点
    stop_reference: str = "signal_bar"     # signal_bar / structure
    entry_style: str = "signal"            # signal / background_limit
    structure_stop_level: float | None = None


def risk_distance(plan: TradePlan) -> float:
    return abs(plan.entry - plan.stop)


def reward_distance(plan: TradePlan) -> float:
    return abs(plan.tp1 - plan.entry)


def risk_reward(plan: TradePlan) -> float:
    risk = risk_distance(plan)
    return reward_distance(plan) / risk if risk > 0 else 0.0


def widen_stop_for_rr_cap(
    plan: TradePlan, target_rr: float | None = None
) -> tuple[TradePlan, dict]:
    """RR 高于目标时把止损向外扩，使 RR 落到 ``target_rr``；外扩不超过尖峰高度 50%。

    ``target_rr`` 由档位提供（见 `engine/stance.py`）；不传时用兜底 1.0，
    保持纯函数在无档位上下文中的既有行为。

    返回 (新方案, 证据)。RR≤目标或几何无法修正时原样返回。
    """
    target = Risk.MAX_TP1_RISK_REWARD_RATIO if target_rr is None else float(target_rr)
    rr = risk_reward(plan)
    evidence = {
        "rr_before": round(rr, 4),
        "risk_before": round(risk_distance(plan), 6),
        "target_rr": round(target, 4),
        "changed": False,
        "capped": False,
    }
    if plan.direction not in ("bullish", "bearish") or risk_distance(plan) <= 0:
        return plan, evidence
    if rr <= target + 1e-9:
        return plan, evidence

    reward = reward_distance(plan)
    target_risk = reward / target if target > 0 else reward
    cap = None
    if plan.spike_height and plan.spike_height > 0:
        cap = plan.spike_height * Risk.WIDEN_STOP_MAX_SPIKE_FRACTION
        if target_risk > cap:
            target_risk = cap
            evidence["capped"] = True
    if target_risk <= risk_distance(plan) + 1e-12:
        return plan, evidence

    new_stop = (
        plan.entry - target_risk if plan.direction == "bullish" else plan.entry + target_risk
    )
    widened = replace(plan, stop=new_stop, widened=True)
    evidence.update(
        {
            "changed": True,
            "stop_before": round(plan.stop, 6),
            "stop_after": round(new_stop, 6),
            "risk_after": round(target_risk, 6),
            "rr_after": round(risk_reward(widened), 4),
            "cap": None if cap is None else round(cap, 6),
        }
    )
    return widened, evidence


def prepare_plan(plan: TradePlan, ctx: GateContext) -> TradePlan:
    """按当前档位外扩止损并写入 ctx，供 §10.1–10.3 共用同一份方案。"""
    policy = _policy(ctx)
    widened, evidence = widen_stop_for_rr_cap(plan, policy.target_rr)
    evidence["stance"] = policy.name
    ctx.extras["trade_plan"] = widened
    ctx.extras["stop_widen"] = evidence
    return widened


def _plan_of(ctx: GateContext) -> TradePlan | None:
    plan = ctx.extras.get("trade_plan")
    return plan if isinstance(plan, TradePlan) else None


@register("10.1")
def gate_10_1(frame: FrameLike, ctx: GateContext) -> GateResult:
    plan = _plan_of(ctx)
    if plan is None:
        return GateResult("10.1", False, "no_plan", 0.95, {}, "无候选方案，止损明确性不适用")
    tick = plan.tick if plan.tick and plan.tick > 0 else 0.0
    geometry_ok = (
        plan.stop < plan.entry if plan.direction == "bullish" else plan.stop > plan.entry
    )
    eps = max(tick * 1e-6, 1e-9) if tick else 1e-9
    outside_ok = True
    branch = "clear"
    structure_ref = plan.stop_reference == "structure" and plan.structure_stop_level is not None

    if structure_ref:
        level = float(plan.structure_stop_level)
        if plan.direction == "bullish":
            outside_ok = plan.stop <= level + eps
        else:
            outside_ok = plan.stop >= level - eps
        if not outside_ok:
            branch = "inside_structure"
    elif plan.signal_bar_extreme is not None and tick:
        if plan.direction == "bullish":
            outside_ok = plan.stop <= plan.signal_bar_extreme - tick + eps
        else:
            outside_ok = plan.stop >= plan.signal_bar_extreme + tick - eps
        if not outside_ok:
            branch = "inside_signal_bar"
    if not geometry_ok:
        branch = "wrong_side"
    answer = geometry_ok and outside_ok
    reference = "结构失效位" if structure_ref else "信号棒极点"
    ref_value = plan.structure_stop_level if structure_ref else plan.signal_bar_extreme
    return GateResult(
        node_id="10.1",
        answer=answer,
        branch=branch,
        confidence=0.9 if (structure_ref or plan.signal_bar_extreme is not None) else 0.7,
        evidence={
            "entry": plan.entry,
            "stop": plan.stop,
            "stop_reference": plan.stop_reference,
            "entry_style": plan.entry_style,
            "structure_stop_level": plan.structure_stop_level,
            "signal_bar_extreme": plan.signal_bar_extreme,
            "signal_bar_seq": plan.signal_bar_seq,
            "tick": tick,
            "risk": round(risk_distance(plan), 6),
        },
        reason=(
            f"止损{plan.stop:.6g}位于{reference}"
            f"{'外' if outside_ok else '内'}"
            f"（参考{ref_value}）→{'明确' if answer else '不明确'}"
            if structure_ref or plan.signal_bar_extreme is not None
            else f"仅有几何止损{plan.stop:.6g}（结构参考缺失），按低置信通过"
        ),
    )


@register("10.2")
def gate_10_2(frame: FrameLike, ctx: GateContext) -> GateResult:
    plan = _plan_of(ctx)
    if plan is None:
        return GateResult("10.2", False, "no_plan", 0.95, {}, "无候选方案，止损大小不适用")
    policy = _policy(ctx)
    min_rr = policy.min_rr
    risk = risk_distance(plan)
    rr = risk_reward(plan)
    checks: dict[str, tuple[bool, float | None]] = {
        "rr_below_1": (rr < min_rr - 1e-9, abs(rr - min_rr)),
        "channel_width": (
            plan.channel_width is not None and risk > plan.channel_width * Risk.STOP_FILTER_CHANNEL_WIDTH,
            None if plan.channel_width is None else abs(risk - plan.channel_width * Risk.STOP_FILTER_CHANNEL_WIDTH),
        ),
        "range_width": (
            plan.range_width is not None and risk > plan.range_width * Risk.STOP_FILTER_RANGE_WIDTH,
            None if plan.range_width is None else abs(risk - plan.range_width * Risk.STOP_FILTER_RANGE_WIDTH),
        ),
        "atr": (
            plan.atr is not None and risk > plan.atr * Risk.STOP_FILTER_ATR_MULT,
            None if plan.atr is None else abs(risk - plan.atr * Risk.STOP_FILTER_ATR_MULT),
        ),
    }
    triggered = [name for name, (hit, _) in checks.items() if hit]
    too_large = bool(triggered)

    margin = None
    for name, (_, gap) in checks.items():
        if gap is None:
            continue
        base = {
            "rr_below_1": min_rr,
            "channel_width": (plan.channel_width or 0) * Risk.STOP_FILTER_CHANNEL_WIDTH,
            "range_width": (plan.range_width or 0) * Risk.STOP_FILTER_RANGE_WIDTH,
            "atr": (plan.atr or 0) * Risk.STOP_FILTER_ATR_MULT,
        }[name]
        if base <= 0:
            continue
        rel = gap / base
        margin = rel if margin is None else min(margin, rel)
    conf = 0.9
    if margin is not None and margin < 0.15:
        conf = 0.6

    return GateResult(
        node_id="10.2",
        answer=too_large,
        branch=triggered[0] if triggered else "ok",
        confidence=conf,
        evidence={
            "risk": round(risk, 6),
            "rr": round(rr, 4),
            "min_rr": min_rr,
            "stance": policy.name,
            "channel_width": plan.channel_width,
            "range_width": plan.range_width,
            "atr": plan.atr,
            "triggered": triggered,
            "limits": {
                "channel": None if plan.channel_width is None else round(plan.channel_width * Risk.STOP_FILTER_CHANNEL_WIDTH, 6),
                "range": None if plan.range_width is None else round(plan.range_width * Risk.STOP_FILTER_RANGE_WIDTH, 6),
                "atr": None if plan.atr is None else round(plan.atr * Risk.STOP_FILTER_ATR_MULT, 6),
            },
        },
        reason=(
            f"风险{risk:.4g}触发{'/'.join(triggered)}（{policy.label_zh}档：RR≥{min_rr:g}）→ 止损过大"
            if triggered
            else f"风险{risk:.4g}（RR={rr:.2f}≥{min_rr:g}，{policy.label_zh}档）未触发四条件 → 止损可接受"
        ),
    )


@register("10.3")
def gate_10_3(frame: FrameLike, ctx: GateContext) -> GateResult:
    plan = _plan_of(ctx)
    if plan is None:
        return GateResult("10.3", False, "no_plan", 0.95, {}, "无候选方案，交易者方程不适用")
    if plan.estimated_win_rate <= 0:
        return GateResult(
            "10.3",
            False,
            "missing_win_rate",
            0.6,
            {"entry": plan.entry, "stop": plan.stop, "tp1": plan.tp1},
            "缺少估算胜率，交易者方程需灰区复核",
        )
    policy = _policy(ctx)
    risk = risk_distance(plan)
    reward = reward_distance(plan)
    rr = risk_reward(plan)
    win = plan.estimated_win_rate
    left = win * reward
    right = (100.0 - win) * risk
    equation_ok = left > right
    # 带符号余量：负数表示期望为负；档位决定可接受的余量下限（极度激进允许轻微负值）。
    gap = (left - right) / right if right > 0 else 1.0
    rr_ok = rr >= policy.min_rr - 1e-9
    gap_ok = gap >= policy.min_equation_gap - 1e-9
    # 独立胜率下限（v1 trade_confidence 门槛的代码化）：方程允许低胜率高赔付，
    # 该下限按档位控制「接受多低胜率的赌单」，是比方程更早的执行层信心闸门。
    win_ok = win >= policy.min_win_rate - 1e-9
    answer = rr_ok and gap_ok and win_ok
    if not rr_ok:
        branch = "rr_below_1"
    elif not gap_ok:
        branch = "equation_failed"
    elif not win_ok:
        branch = "low_win_rate"
    else:
        branch = "passed"
    conf = 0.6 if abs(gap) < 0.05 else 0.95
    return GateResult(
        node_id="10.3",
        answer=answer,
        branch=branch,
        confidence=conf,
        evidence={
            "entry": plan.entry,
            "stop": plan.stop,
            "tp1": plan.tp1,
            "tp2": plan.tp2,
            "risk": round(risk, 6),
            "reward": round(reward, 6),
            "rr": round(rr, 4),
            "min_rr": policy.min_rr,
            "equation_gap": round(gap, 4),
            "min_equation_gap": policy.min_equation_gap,
            "min_win_rate": policy.min_win_rate,
            "stance": policy.name,
            "estimated_win_rate": win,
            "equation_left": round(left, 6),
            "equation_right": round(right, 6),
            "widened": plan.widened,
            "stop_widen": ctx.extras.get("stop_widen", {}),
        },
        reason=(
            f"RR={rr:.2f}（≥{policy.min_rr:g}）、胜率{win:.0f}%（下限{policy.min_win_rate:g}%）："
            f"{win:.0f}%×{reward:.4g}={left:.4g} "
            f"{'>' if equation_ok else '≤'} {(100 - win):.0f}%×{risk:.4g}={right:.4g}；"
            f"方程余量{gap:+.1%}（{policy.label_zh}档下限{policy.min_equation_gap:+.0%}）"
            f" → {'通过' if answer else '不通过'}"
        ),
    )


__all__ = [
    "Risk",
    "TradePlan",
    "risk_distance",
    "reward_distance",
    "risk_reward",
    "widen_stop_for_rr_cap",
    "prepare_plan",
    "gate_10_1",
    "gate_10_2",
    "gate_10_3",
]
