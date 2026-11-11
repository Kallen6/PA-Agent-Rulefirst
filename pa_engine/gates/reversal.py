"""§7 反转识别（B 类：代码出证据，仅诊断、禁止逆势下单）。

节点映射（二元决策.txt §7）：
    §7.1 是否只有单根反转K线？       最近 3 根中的反转棒计数
    §7.2 是否同时满足两大反转条件？  趋势线收盘突破 + 前极点测试失败
    §7.3 是否出现二次入场？          H2/L2（复用 HLCountState）
    §7.4 二次入场是否失败？          收盘跌破/升破结构失效位

v1 原文明确：§7 只提高反转**诊断**置信度，**禁止逆势入场**。
因此本模块只输出 `reversal_attempt` 标记，是否禁止下单由流水线/§14 守卫裁定。
"""
from __future__ import annotations

from dataclasses import dataclass, replace as _dc_replace

from pa_engine.config.thresholds import Reversal
from pa_engine.data.types import KlineBar
from pa_engine.features.structure import SwingPivot, find_confirmed_swings
from pa_engine.gates.base import (
    GateContext,
    GateResult,
    FrameLike,
    atr_of,
    register,
    structure_of,
)


@dataclass(frozen=True)
class ReversalAssessment:
    trend: str
    single_bar: bool
    reversal_bars: int
    trendline_broken: bool
    failed_extreme_test: bool
    confirmed: bool
    line_value: float | None
    prior_extreme: float | None
    latest_extreme: float | None
    confidence: float
    reason: str


def _chrono_pivots(frame: FrameLike) -> list[SwingPivot]:
    pivots = find_confirmed_swings(getattr(frame, "bars", ()) or (), Reversal.LOOKBACK)
    return sorted(pivots, key=lambda p: p.seq, reverse=True)


def _trend_of(frame: FrameLike, ctx: GateContext) -> str:
    for key in ("background_direction", "direction"):
        value = str(ctx.extras.get(key) or "")
        if value in ("bullish", "bearish"):
            return value
    sf = structure_of(frame, ctx)
    if sf.swing_structure == "HH+HL":
        return "bullish"
    if sf.swing_structure == "LL+LH":
        return "bearish"
    return "neutral"


def _is_reversal_bar(bar: KlineBar, trend: str) -> bool:
    high = float(bar.high)
    low = float(bar.low)
    open_ = float(bar.open)
    close = float(bar.close)
    full = high - low
    if full <= 0:
        return False
    body = abs(close - open_) / full
    if body < Reversal.REVERSAL_BODY_MIN:
        return False
    if trend == "bullish":
        return close < open_ and (close - low) / full <= Reversal.REVERSAL_CLOSE_POS
    if trend == "bearish":
        return close > open_ and (high - close) / full <= Reversal.REVERSAL_CLOSE_POS
    return False


def _project_trendline(pivots: list[SwingPivot], kind: str, at_seq: int = 1) -> float | None:
    same = [p for p in pivots if p.kind == kind]
    if len(same) < Reversal.TRENDLINE_MIN_SWINGS:
        return None
    # 跌破/升破当根形成的极点在 same 末尾，趋势线应取它之前的两点
    older, newer = (same[-3], same[-2]) if len(same) >= 3 else (same[0], same[-1])
    span = float(newer.seq - older.seq)
    if abs(span) < 1e-9:
        return None
    slope = (newer.price - older.price) / span
    return newer.price + slope * (at_seq - newer.seq)


def assess_reversal(frame: FrameLike, ctx: GateContext) -> ReversalAssessment:
    cached = ctx.extras.get("reversal_assessment")
    if isinstance(cached, ReversalAssessment):
        return cached

    bars = getattr(frame, "bars", ()) or ()
    trend = _trend_of(frame, ctx)
    atr = atr_of(frame, ctx) or 0.0
    pivots = _chrono_pivots(frame)
    reversal_bars = (
        sum(1 for bar in bars[: Reversal.SINGLE_BAR_WINDOW] if _is_reversal_bar(bar, trend))
        if trend in ("bullish", "bearish")
        else 0
    )

    line_value = None
    trendline_broken = False
    failed_test = False
    prior_extreme = None
    latest_extreme = None

    if trend == "bullish":
        line_value = _project_trendline(pivots, "low")
        if line_value is not None and bars:
            trendline_broken = float(bars[0].close) < line_value
        highs = [p for p in pivots if p.kind == "high"]
        if len(highs) >= 2:
            latest_extreme = highs[-1].price
            prior_extreme = highs[-2].price
            failed_test = latest_extreme <= prior_extreme + Reversal.FAILED_TEST_TOL_ATR * atr
    elif trend == "bearish":
        line_value = _project_trendline(pivots, "high")
        if line_value is not None and bars:
            trendline_broken = float(bars[0].close) > line_value
        lows = [p for p in pivots if p.kind == "low"]
        if len(lows) >= 2:
            latest_extreme = lows[-1].price
            prior_extreme = lows[-2].price
            failed_test = latest_extreme >= prior_extreme - Reversal.FAILED_TEST_TOL_ATR * atr

    confirmed = bool(trendline_broken and failed_test)
    # 清晰否定给高置信；边界（单一条件成立/极值贴近）给低置信进入灰区
    if trend == "neutral":
        confidence = 0.85
    elif confirmed or not trendline_broken:
        confidence = 0.85
    else:
        confidence = 0.55
    reason = (
        f"{trend}趋势：趋势线{'破' if trendline_broken else '未破'}、"
        f"前极点{'测试失败' if failed_test else '未失败'} → "
        f"{'反转尝试（仅诊断）' if confirmed else '非反转，按回撤/旗形处理'}"
    )
    result = ReversalAssessment(
        trend=trend,
        single_bar=reversal_bars == 1,
        reversal_bars=reversal_bars,
        trendline_broken=trendline_broken,
        failed_extreme_test=failed_test,
        confirmed=confirmed,
        line_value=None if line_value is None else round(line_value, 6),
        prior_extreme=prior_extreme,
        latest_extreme=latest_extreme,
        confidence=confidence,
        reason=reason,
    )
    ctx.extras["reversal_assessment"] = result
    return result


@register("7.1")
def gate_7_1(frame: FrameLike, ctx: GateContext) -> GateResult:
    a = assess_reversal(frame, ctx)
    branch = "single" if a.single_bar else ("none" if a.reversal_bars == 0 else "multiple")
    return GateResult(
        node_id="7.1",
        answer=a.single_bar,
        branch=branch,
        confidence=0.85 if a.reversal_bars != 1 else 0.75,
        evidence={
            "trend": a.trend,
            "reversal_bars": a.reversal_bars,
            "window": Reversal.SINGLE_BAR_WINDOW,
        },
        reason=(
            f"近{Reversal.SINGLE_BAR_WINDOW}根有{a.reversal_bars}根反转棒 → "
            f"{'单根不足以反转' if a.single_bar else '非单根反转场景'}"
        ),
    )


@register("7.2")
def gate_7_2(frame: FrameLike, ctx: GateContext) -> GateResult:
    a = assess_reversal(frame, ctx)
    # v1 文件20：20GB 后首次触 EMA 非反转 → 不标记 reversal_attempt，按回撤处理
    if a.confirmed and ctx.extras.get("twenty_gb_first_touch") is True:
        a = _dc_replace(
            a,
            confirmed=False,
            confidence=0.7,
            reason=a.reason + "；20GB 首次触 EMA，按回撤处理而非反转",
        )
    branch = "reversal_attempt" if a.confirmed else ("line_broken" if a.trendline_broken else "no")
    ctx.extras["reversal_attempt"] = a.confirmed
    return GateResult(
        node_id="7.2",
        answer=a.confirmed,
        branch=branch,
        confidence=a.confidence,
        evidence={
            "trend": a.trend,
            "trendline_broken": a.trendline_broken,
            "failed_extreme_test": a.failed_extreme_test,
            "trendline_value": a.line_value,
            "prior_extreme": a.prior_extreme,
            "latest_extreme": a.latest_extreme,
        },
        reason=a.reason,
    )


@register("7.3")
def gate_7_3(frame: FrameLike, ctx: GateContext) -> GateResult:
    direction = str(ctx.extras.get("direction") or "neutral")
    if direction not in ("bullish", "bearish"):
        return GateResult("7.3", False, "no_direction", 0.85, {}, "方向未定，二次入场不适用")
    sf = structure_of(frame, ctx)
    count = sf.hl_count.bull_count if direction == "bullish" else sf.hl_count.bear_count
    answer = count >= Reversal.SECOND_ENTRY_MIN_HL
    branch = "second" if answer else ("third+" if count >= 3 else "none")
    return GateResult(
        node_id="7.3",
        answer=answer,
        branch=branch,
        confidence=0.75,
        evidence={
            "direction": direction,
            "hl_count": count,
            "candidate": (
                sf.hl_count.bull_candidate if direction == "bullish" else sf.hl_count.bear_candidate
            ),
        },
        reason=f"{direction} H/L计数={count} → {'二次入场' if answer else '尚无二次入场'}",
    )


@register("7.4")
def gate_7_4(frame: FrameLike, ctx: GateContext) -> GateResult:
    direction = str(ctx.extras.get("direction") or "neutral")
    sf = structure_of(frame, ctx)
    count = (
        sf.hl_count.bull_count
        if direction == "bullish"
        else sf.hl_count.bear_count
        if direction == "bearish"
        else 0
    )
    if count < Reversal.SECOND_ENTRY_MIN_HL:
        return GateResult(
            "7.4", False, "n/a", 0.9, {"hl_count": count}, "无二次入场，失败判定不适用"
        )
    bars = getattr(frame, "bars", ()) or ()
    close = float(bars[0].close) if bars else 0.0
    invalidation = sf.invalidation_long if direction == "bullish" else sf.invalidation_short
    if invalidation is None:
        return GateResult(
            "7.4", False, "unknown", 0.55, {"invalidation": None}, "缺少结构失效位，二次入场失败待定"
        )
    failed = close < invalidation if direction == "bullish" else close > invalidation
    return GateResult(
        node_id="7.4",
        answer=failed,
        branch="failed" if failed else "intact",
        confidence=0.8,
        evidence={"invalidation": invalidation, "close": close, "hl_count": count},
        reason=(
            f"二次入场后收盘{close} {'跌破' if direction == 'bullish' else '升破'}失效位{invalidation}"
            f" → {'失败，禁止第三次尝试' if failed else '结构仍有效'}"
        ),
    )
