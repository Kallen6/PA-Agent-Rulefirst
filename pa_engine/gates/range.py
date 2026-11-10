"""§6 交易区间闸门。

A 类主判：§6.3 边界三分位 / §6.4 Barbwire / §6.5 突破
B 类证据：§6.1 边界清晰度 / §6.2 区间类型 / §6.6 三角形（不改变周期分类）
"""
from __future__ import annotations

from pa_engine.config.thresholds import Range, Structure, Triangle
from pa_engine.features.structure import SwingPivot, find_confirmed_swings
from pa_engine.gates.base import (
    GateContext,
    GateResult,
    FrameLike,
    atr_of,
    register,
    structure_of,
)
from pa_engine.gates.common import swing_groups


@register("6.3")
def gate_6_3(frame: FrameLike, ctx: GateContext) -> GateResult:
    sf = structure_of(frame, ctx)
    zone = sf.zone
    at_boundary = zone in ("lower_third", "upper_third")
    position = sf.price_position
    if position is None:
        conf = 0.4
    else:
        dist_to_edge = min(abs(position - 1 / 3), abs(position - 2 / 3))
        conf = 0.9 if dist_to_edge >= 0.08 else 0.6
    branch = {
        "lower_third": "lower_boundary",
        "upper_third": "upper_boundary",
        "middle_third": "middle_third",
    }.get(zone, "unknown")
    return GateResult(
        node_id="6.3",
        answer=at_boundary,
        branch=branch,
        confidence=conf,
        evidence={
            "zone": zone,
            "price_position": position,
            "range_high": sf.range_high,
            "range_low": sf.range_low,
        },
        reason=(
            f"收盘位于区间{zone}（位置{position:.2f}）→"
            f"{'边界' if at_boundary else '中部'}"
            if position is not None
            else "区间数据不足"
        ),
    )


@register("6.4")
def gate_6_4(frame: FrameLike, ctx: GateContext) -> GateResult:
    sf = structure_of(frame, ctx)
    barbwire = bool(sf.barbwire_candidate)
    return GateResult(
        node_id="6.4",
        answer=barbwire,
        branch="barbwire" if barbwire else "no",
        confidence=0.8 if abs(sf.barbwire_score - 0.6) > 0.1 else 0.6,
        evidence={
            "barbwire_score": sf.barbwire_score,
            "overlap_mean_10": sf.overlap_mean_10,
            "doji_inside_ratio_10": sf.doji_inside_ratio_10,
            "range_width_atr": sf.range_width_atr,
        },
        reason=(
            f"铁丝网评分{sf.barbwire_score:.2f}（阈值0.6），"
            f"重叠{sf.overlap_mean_10}，宽度{sf.range_width_atr}ATR"
            f"→{'Barbwire' if barbwire else '非Barbwire'}"
        ),
    )


@register("6.5")
def gate_6_5(frame: FrameLike, ctx: GateContext) -> GateResult:
    sf = structure_of(frame, ctx)
    quality = sf.breakout_quality
    occurred = quality in ("close_breakout", "surviving", "failed")
    return GateResult(
        node_id="6.5",
        answer=occurred,
        branch=quality,
        confidence=0.85 if quality != "wick_probe" else 0.55,
        evidence={
            "breakout_quality": quality,
            "attempt_type": sf.breakout_attempt_type,
            "events": [
                {
                    "level": e.level_price,
                    "kind": e.level_kind,
                    "event": e.event,
                    "seq": e.trigger_seq,
                }
                for e in sf.breakout_events[:4]
            ],
            "mm_as_tp_ok": sf.mm_as_tp_ok,
        },
        reason=f"突破质量={quality}（影线试探/无突破不计为有效突破）",
    )


def _boundary_touches(frame: FrameLike, ctx: GateContext) -> tuple[int, int]:
    sf = structure_of(frame, ctx)
    bars = getattr(frame, "bars", ()) or ()
    atr = atr_of(frame, ctx) or 0.0
    if sf.range_high is None or sf.range_low is None or atr <= 0:
        return 0, 0
    tol = atr * Range.BOUNDARY_TOUCH_TOL_ATR
    high, low = float(sf.range_high), float(sf.range_low)
    high_touch = sum(1 for b in bars if float(b.high) >= high - tol)
    low_touch = sum(1 for b in bars if float(b.low) <= low + tol)
    return high_touch, low_touch


@register("6.1")
def gate_6_1(frame: FrameLike, ctx: GateContext) -> GateResult:
    sf = structure_of(frame, ctx)
    high_touch, low_touch = _boundary_touches(frame, ctx)
    width_ok = (
        sf.range_width_atr is not None
        and Range.CLEAR_WIDTH_MIN_ATR <= sf.range_width_atr <= Range.CLEAR_WIDTH_MAX_ATR
    )
    clear = bool(
        width_ok
        and high_touch >= Range.BOUNDARY_TOUCH_MIN
        and low_touch >= Range.BOUNDARY_TOUCH_MIN
    )
    confidence = 0.85 if clear else 0.6
    return GateResult(
        node_id="6.1",
        answer=clear,
        branch="clear" if clear else ("too_tight" if sf.range_width_atr and sf.range_width_atr < Range.CLEAR_WIDTH_MIN_ATR else "unclear"),
        confidence=confidence,
        evidence={
            "range_high": sf.range_high,
            "range_low": sf.range_low,
            "range_width_atr": sf.range_width_atr,
            "high_touches": high_touch,
            "low_touches": low_touch,
            "touch_tolerance_atr": Range.BOUNDARY_TOUCH_TOL_ATR,
        },
        reason=(
            f"上边界触碰{high_touch}次、下边界{low_touch}次，宽度{sf.range_width_atr}ATR → "
            f"{'边界清晰' if clear else '边界不够清晰'}"
        ),
    )


@register("6.2")
def gate_6_2(frame: FrameLike, ctx: GateContext) -> GateResult:
    sf = structure_of(frame, ctx)
    groups, groups_dir = swing_groups(frame.bars, Structure.LOOKBACK)
    trending = bool(
        groups >= Range.TRENDING_GROUPS_MIN
        and sf.swing_structure in ("HH+HL", "LL+LH")
        and groups_dir in ("bullish", "bearish")
    )
    branch = "trending_tr" if trending else "trading_range"
    return GateResult(
        node_id="6.2",
        answer=branch,
        branch=branch,
        confidence=0.8 if groups >= 2 else 0.65,
        evidence={
            "hh_hl_groups": groups,
            "groups_direction": groups_dir,
            "swing_structure": sf.swing_structure,
        },
        reason=(
            f"{groups}组{groups_dir}摆动 → {'趋势型交易区间' if trending else '普通交易区间'}"
        ),
    )


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


@register("6.6")
def gate_6_6(frame: FrameLike, ctx: GateContext) -> GateResult:
    """三角形：收敛/扩张边界识别，只写 pattern 证据，不改变周期。"""
    bars = getattr(frame, "bars", ()) or ()
    pivots = sorted(
        find_confirmed_swings(bars, Triangle.LOOKBACK), key=lambda p: p.seq, reverse=True
    )
    pivots = _alternating(pivots)
    pattern = "none"
    contraction = None
    atr = atr_of(frame, ctx) or 0.0

    if len(pivots) >= Triangle.MIN_SWINGS:
        highs = [p.price for p in pivots if p.kind == "high"][-3:]
        lows = [p.price for p in pivots if p.kind == "low"][-3:]
        if len(highs) >= 2 and len(lows) >= 2:
            first_width = highs[0] - lows[0]
            last_width = highs[-1] - lows[-1]
            if first_width > 0:
                contraction = round(last_width / first_width, 4)
            flat = atr * Triangle.FLAT_SIDE_ATR
            descending_highs = highs[-1] < highs[0] - flat
            ascending_lows = lows[-1] > lows[0] + flat
            expanding = highs[-1] > highs[0] + flat and lows[-1] < lows[0] - flat
            ascending_triangle = abs(highs[-1] - highs[0]) <= flat and ascending_lows
            descending_triangle = abs(lows[-1] - lows[0]) <= flat and descending_highs
            if expanding:
                pattern = "expanding_triangle"
            elif ascending_triangle:
                pattern = "ascending_triangle"
            elif descending_triangle:
                pattern = "descending_triangle"
            elif (
                descending_highs
                and ascending_lows
                and contraction is not None
                and contraction <= Triangle.CONTRACTION_RATIO
            ):
                pattern = "symmetrical_triangle"

    if pattern == "none":
        confidence = 0.85
    elif pattern == "expanding_triangle":
        confidence = 0.7
    else:
        confidence = 0.75
    if pattern != "none":
        ctx.extras.setdefault("patterns", []).append(pattern)
    return GateResult(
        node_id="6.6",
        answer=pattern != "none",
        branch=pattern,
        confidence=confidence,
        evidence={
            "pattern": pattern,
            "contraction": contraction,
            "swings": len(pivots),
            "highs": [p.price for p in pivots if p.kind == "high"][-3:],
            "lows": [p.price for p in pivots if p.kind == "low"][-3:],
        },
        reason=(
            f"收敛比{contraction} → {pattern}"
            if pattern != "none"
            else "边界未形成三角形收敛/扩张"
        ),
    )
