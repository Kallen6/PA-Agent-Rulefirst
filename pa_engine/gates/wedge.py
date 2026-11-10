"""§8 楔形 / 三推（B 类：结构证据 + 硬禁令）。

节点映射（二元决策.txt §8）：
    §8.1 是否有三推结构？        3 个同向推进 + 动能递减 + 边界收敛
    §8.2 楔形是回撤还是反转？    楔形方向 vs 主趋势方向
    §8.3 是否为抛物线楔形？      末推斜率加速 / 高潮

v1 原文：楔形反转仅诊断，禁止追顺势（含 H1/H2、追突破）、禁止逆势下单；
抛物线楔形禁止追趋势/追高潮。本模块输出 `wedge_type` 与 `parabolic`，
具体禁令由流水线与 §14 守卫执行。
"""
from __future__ import annotations

from dataclasses import dataclass

from pa_engine.config.thresholds import Wedge
from pa_engine.features.structure import SwingPivot, find_confirmed_swings
from pa_engine.gates.base import (
    GateContext,
    GateResult,
    FrameLike,
    register,
    structure_of,
)


@dataclass(frozen=True)
class WedgeAssessment:
    three_push: bool
    wedge_direction: str          # bullish 上升楔形 / bearish 下降楔形 / none
    pushes: tuple[float, ...]
    slopes: tuple[float, ...]
    contraction: float | None
    decay: bool
    parabolic: bool
    reason: str
    confidence: float


def _alternating(pivots: list[SwingPivot]) -> list[SwingPivot]:
    cleaned: list[SwingPivot] = []
    for pivot in pivots:
        if not cleaned or cleaned[-1].kind != pivot.kind:
            cleaned.append(pivot)
            continue
        previous = cleaned[-1]
        more_extreme = (
            pivot.price > previous.price if pivot.kind == "high" else pivot.price < previous.price
        )
        if more_extreme:
            cleaned[-1] = pivot
    return cleaned


def _find_pattern(pivots: list[SwingPivot]) -> list[SwingPivot]:
    """返回最近 6 个交替枢轴（3 高 + 3 低），否则空列表。"""
    for end in range(len(pivots), 5, -1):
        window = pivots[end - 6 : end]
        kinds = "".join("H" if p.kind == "high" else "L" for p in window)
        if kinds in ("HLHLHL", "LHLHLH"):
            return window
    return []


def _slope(height: float, span: int) -> float:
    return height / span if span > 0 else 0.0


def assess_wedge(frame: FrameLike, ctx: GateContext) -> WedgeAssessment:
    cached = ctx.extras.get("wedge_assessment")
    if isinstance(cached, WedgeAssessment):
        return cached

    bars = getattr(frame, "bars", ()) or ()
    pivots = sorted(
        find_confirmed_swings(bars, Wedge.LOOKBACK), key=lambda p: p.seq, reverse=True
    )
    pivots = _alternating(pivots)
    window = _find_pattern(pivots)
    direction = "none"
    pushes: list[float] = []
    slopes: list[float] = []
    contraction = None
    decay = False
    rising_or_falling = False

    if window:
        highs = [p for p in window if p.kind == "high"]
        lows = [p for p in window if p.kind == "low"]
        h0, h1, h2 = highs
        l0, l1, l2 = lows
        bull = h2.price > h1.price > h0.price and l2.price > l1.price > l0.price
        bear = h2.price < h1.price < h0.price and l2.price < l1.price < l0.price
        if bull:
            direction = "bullish"
        elif bear:
            direction = "bearish"
        if bull or bear:
            rising_or_falling = True
            pushes = [h0.price - l0.price, h1.price - l1.price, h2.price - l2.price]
            slopes = [
                _slope(pushes[0], abs(h0.seq - l0.seq)),
                _slope(pushes[1], abs(h1.seq - l1.seq)),
                _slope(pushes[2], abs(h2.seq - l2.seq)),
            ]
            if pushes[0] > 0:
                contraction = round(pushes[2] / pushes[0], 4)
                decay = pushes[1] <= pushes[0] and pushes[2] <= pushes[1]

    three_push = bool(
        pushes
        and rising_or_falling
        and decay
        and contraction is not None
        and contraction <= Wedge.CONTRACTION_RATIO
    )
    parabolic = bool(
        pushes
        and rising_or_falling
        and len(slopes) == 3
        and slopes[0] > 0
        and slopes[2] / slopes[0] >= Wedge.PARABOLIC_SLOPE_RATIO
    )
    if parabolic:
        ctx.extras["parabolic"] = True

    if not pivots:
        confidence = 0.85
        reason = "摆动点不足，无法识别楔形"
    elif direction == "none":
        confidence = 0.85 if len(pivots) < 5 else 0.6
        reason = "未形成 5 段交替的 HLHLH/LHLHL 结构"
    elif three_push:
        confidence = 0.8
        reason = (
            f"{'上升' if direction == 'bullish' else '下降'}楔形：3 推幅度"
            f"{[round(x, 4) for x in pushes]}，收敛{contraction:.0%}，动能递减"
        )
    else:
        confidence = 0.55
        reason = (
            f"疑似{'上升' if direction == 'bullish' else '下降'}楔形但"
            f"收敛{contraction if contraction is None else round(contraction, 3)}/动能递减{decay}不满足"
        )

    result = WedgeAssessment(
        three_push=three_push,
        wedge_direction=direction if three_push else "none",
        pushes=tuple(round(x, 6) for x in pushes),
        slopes=tuple(round(x, 6) for x in slopes),
        contraction=contraction,
        decay=decay,
        parabolic=parabolic,
        reason=reason,
        confidence=confidence,
    )
    ctx.extras["wedge_assessment"] = result
    return result


def _main_trend(ctx: GateContext) -> str:
    for key in ("background_direction", "direction"):
        value = str(ctx.extras.get(key) or "")
        if value in ("bullish", "bearish"):
            return value
    return "neutral"


@register("8.1")
def gate_8_1(frame: FrameLike, ctx: GateContext) -> GateResult:
    a = assess_wedge(frame, ctx)
    return GateResult(
        node_id="8.1",
        answer=a.three_push,
        branch="three_push" if a.three_push else ("insufficient" if not a.pushes else "no"),
        confidence=a.confidence,
        evidence={
            "pushes": list(a.pushes),
            "slopes": list(a.slopes),
            "contraction": a.contraction,
            "decay": a.decay,
            "lookback": Wedge.LOOKBACK,
        },
        reason=a.reason,
    )


@register("8.2")
def gate_8_2(frame: FrameLike, ctx: GateContext) -> GateResult:
    a = assess_wedge(frame, ctx)
    if not a.three_push:
        return GateResult(
            "8.2", "n/a", "n/a", 0.9, {"three_push": False}, "无三推楔形，回撤/反转判定不适用"
        )
    main = _main_trend(ctx)
    if main == "neutral":
        wedge_type = "unknown"
        confidence = 0.55
    elif main == a.wedge_direction:
        wedge_type = "reversal"
        confidence = 0.6
    else:
        wedge_type = "pullback"
        confidence = 0.8
    ctx.extras["wedge_type"] = wedge_type
    return GateResult(
        node_id="8.2",
        answer=wedge_type,
        branch=wedge_type,
        confidence=confidence,
        evidence={
            "wedge_direction": a.wedge_direction,
            "main_trend": main,
            "three_push": True,
        },
        reason=(
            f"楔形方向{a.wedge_direction} vs 主趋势{main} → "
            f"{'楔形反转（仅诊断，禁止追单/逆势）' if wedge_type == 'reversal' else '楔形回撤（顺主趋势）'}"
            if wedge_type != "unknown"
            else "主趋势中性，楔形性质待定"
        ),
    )


@register("8.3")
def gate_8_3(frame: FrameLike, ctx: GateContext) -> GateResult:
    a = assess_wedge(frame, ctx)
    if len(a.slopes) < 3 or not a.slopes[0]:
        return GateResult(
            "8.3", False, "n/a", 0.9, {"three_push": a.three_push}, "非三推楔形，抛物线判定不适用"
        )
    ratio = a.slopes[2] / a.slopes[0]
    ctx.extras["parabolic"] = a.parabolic
    return GateResult(
        node_id="8.3",
        answer=a.parabolic,
        branch="parabolic" if a.parabolic else "normal",
        confidence=0.8,
        evidence={
            "slope_first": a.slopes[0],
            "slope_last": a.slopes[2],
            "slope_ratio": round(ratio, 4),
            "threshold": Wedge.PARABOLIC_SLOPE_RATIO,
            "three_push": a.three_push,
        },
        reason=(
            f"末推斜率/首推={ratio:.2f}（阈值{Wedge.PARABOLIC_SLOPE_RATIO}）"
            f"→{'抛物线楔形，禁止追单' if a.parabolic else '普通楔形'}"
        ),
    )
