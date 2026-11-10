"""§3 尖峰闸门（A 类，代码判定）。

§3.1 尖峰分级 / §3.2 是否延续 / §3.5 尖峰后三路径 / §3.6 高潮 / §3.7 第几次高潮
判据来源：`prompt_engineering/二元决策.txt §3` + `thresholds.Spike`

尖峰必须同时满足三件事：① 连续同向且实体低重叠；② 相对先前盘整有突破位移；
③ 与当前趋势方向一致（反向低重叠run属于回撤，不是顺势尖峰）。
"""
from __future__ import annotations

from dataclasses import dataclass

from pa_engine.config.thresholds import Spike, Structure
from pa_engine.data.types import KlineBar
from pa_engine.gates.base import (
    GateContext,
    GateResult,
    FrameLike,
    atr_of,
    register,
    structure_of,
)
from pa_engine.gates.common import (
    SpikeRun,
    detect_last_spike_run,
    latest_direction,
    pullback_after_run,
)


@dataclass(frozen=True)
class SpikeAssessment:
    run: SpikeRun | None
    qualifies: bool
    displaced: bool
    aligned: bool
    threshold: float
    prior_range: float | None
    reason: str


def assess_spike(frame: FrameLike, ctx: GateContext) -> SpikeAssessment:
    """一次性完成尖峰三判据，供 §3.1/3.2/3.5/3.6 共用（同一 ctx 内结果一致）。"""
    cached = ctx.extras.get("spike_assessment")
    if isinstance(cached, SpikeAssessment):
        return cached
    atr = atr_of(frame, ctx)
    run = detect_last_spike_run(frame.bars, atr, lookback=Structure.SPIKE_LOOKBACK)
    if run is None:
        result = SpikeAssessment(
            None, False, False, True, 0.0, None, "未识别到连续同向趋势棒"
        )
        ctx.extras["spike_assessment"] = result
        return result

    threshold = (
        Spike.OVERLAP_MAX_CANDIDATE if run.length == 2 else Spike.OVERLAP_MAX_STANDARD
    )
    overlap_ok = run.body_overlap <= threshold
    prior_range = _prior_range(frame.bars, run, Structure.SPIKE_LOOKBACK)
    floor = atr if atr and atr > 0 else 0.0
    base = max(prior_range or 0.0, floor)
    displaced = base <= 0 or run.height >= Spike.SPIKE_DISPLACEMENT_RANGE_MULT * base
    trend = _trend_direction(frame, ctx)
    aligned = trend not in ("bullish", "bearish") or run.direction == trend
    qualifies = bool(
        overlap_ok and displaced and aligned and run.length >= Spike.ROUTABLE_MIN_RUN
    )

    if not overlap_ok:
        reason = (
            f"{run.length}根推进但实体重叠{run.body_overlap:.0%}≥阈值{threshold:.0%}，"
            "属通道而非尖峰"
        )
    elif not aligned:
        reason = f"{run.length}根反向推进（{run.direction} vs {trend}），属回撤而非顺势尖峰"
    elif not displaced:
        reason = (
            f"推进高度{run.height:.2f}未超过先前盘整宽度{base:.2f}，缺乏突破位移"
        )
    else:
        reason = (
            f"{run.length}根{run.direction}趋势棒，重叠{run.body_overlap:.0%}≤{threshold:.0%}，"
            f"高度{run.height:.2f}≥盘整{base:.2f}"
        )
    result = SpikeAssessment(
        run, qualifies, displaced, aligned, threshold, prior_range, reason
    )
    ctx.extras["spike_assessment"] = result
    return result


def _prior_range(bars: tuple[KlineBar, ...], run: SpikeRun, lookback: int) -> float | None:
    older = [b for b in bars if int(b.seq) > run.start_seq][:lookback]
    if not older:
        return None
    return max(float(b.high) for b in older) - min(float(b.low) for b in older)


def _trend_direction(frame: FrameLike, ctx: GateContext) -> str:
    direction = ctx.extras.get("direction")
    if direction in ("bullish", "bearish"):
        return direction
    sf = structure_of(frame, ctx)
    if sf.swing_structure == "HH+HL":
        return "bullish"
    if sf.swing_structure == "LL+LH":
        return "bearish"
    return latest_direction(frame.bars, 8)


@register("3.1")
def gate_3_1(frame: FrameLike, ctx: GateContext) -> GateResult:
    a = assess_spike(frame, ctx)
    if a.run is None:
        return GateResult("3.1", False, "none", 0.85, {"run": None}, a.reason)
    branch = "spike"
    if a.qualifies and a.run.length >= Spike.CLIMAX_MIN_RUN:
        branch = "climax"
    elif not a.qualifies:
        if not a.aligned:
            branch = "pullback"
        elif not a.displaced:
            branch = "not_displaced"
        else:
            branch = "channel"
    conf = 0.9 if a.qualifies else 0.85
    if a.run.body_overlap <= a.threshold:
        conf = min(conf, _confidence_margin(a.run.body_overlap, a.threshold, base=0.9))
    return GateResult(
        node_id="3.1",
        answer=a.qualifies,
        branch=branch,
        confidence=conf,
        evidence={
            "run_length": a.run.length,
            "direction": a.run.direction,
            "body_overlap": a.run.body_overlap,
            "overlap_threshold": a.threshold,
            "height": round(a.run.height, 4),
            "prior_range": None if a.prior_range is None else round(a.prior_range, 4),
            "aligned": a.aligned,
            "displaced": a.displaced,
        },
        reason=a.reason,
    )


@register("3.2")
def gate_3_2(frame: FrameLike, ctx: GateContext) -> GateResult:
    a = assess_spike(frame, ctx)
    if a.run is None or not a.qualifies:
        return GateResult(
            "3.2", False, "no_spike", 0.85, {"run": None}, "非有效尖峰，延续性不适用"
        )
    ongoing = a.run.end_seq == 1
    return GateResult(
        node_id="3.2",
        answer=ongoing,
        branch=a.run.direction if ongoing else "ended",
        confidence=0.85,
        evidence={"run_end_seq": a.run.end_seq, "run_length": a.run.length},
        reason="最新一根仍属尖峰run" if ongoing else f"尖峰run结束于K{a.run.end_seq}",
    )


@register("3.5")
def gate_3_5(frame: FrameLike, ctx: GateContext) -> GateResult:
    a = assess_spike(frame, ctx)
    run = a.run
    if run is None or not a.qualifies:
        return GateResult("3.5", "n/a", "none", 0.85, {"run": None}, "非有效尖峰，三路径不适用")
    pct, extreme = pullback_after_run(frame.bars, run)
    branch = "flag"
    reason = f"回撤{pct:.1%}尖峰高度"
    after = [b for b in frame.bars if int(b.seq) < run.end_seq]
    if not after:
        return GateResult(
            node_id="3.5",
            answer="ongoing",
            branch="ongoing",
            confidence=0.85,
            evidence={
                "pullback_pct": 0.0,
                "pullback_extreme": extreme,
                "spike_origin": run.origin,
                "direction": run.direction,
                "run_end_seq": run.end_seq,
            },
            reason="尖峰仍在进行（无后续K线），三路径待回撤后确认",
        )
    if after:
        last_close = float(after[0].close)
        origin_broken = (
            last_close < run.origin if run.direction == "bullish" else last_close > run.origin
        )
        if origin_broken:
            branch = "sticky_reversal_risk"
            reason += "，且收盘已破尖峰起点（粘性反转风险）"
        elif pct < 0.382:
            branch = "channel"
            reason += " → 浅回撤，通道/旗形延续"
        elif pct > 0.618:
            branch = "range"
            reason += " → 深回撤，进入区间"
        else:
            branch = "flag"
            reason += " → 旗形整理"
    return GateResult(
        node_id="3.5",
        answer=branch,
        branch=branch,
        confidence=0.7,
        evidence={
            "pullback_pct": pct,
            "pullback_extreme": extreme,
            "spike_origin": run.origin,
            "direction": run.direction,
        },
        reason=reason,
    )


@register("3.6")
def gate_3_6(frame: FrameLike, ctx: GateContext) -> GateResult:
    a = assess_spike(frame, ctx)
    if a.run is None or not a.qualifies:
        return GateResult("3.6", False, "no_spike", 0.85, {"run": None}, "非有效尖峰，高潮判定不适用")
    run = a.run
    run_bars = _run_bars(frame.bars, run)
    flags = _exhaustion_flags(run_bars, run.direction)
    triggered = bool(flags)
    branch = "triggered" if triggered else ("warning" if run.length >= Spike.CLIMAX_MIN_RUN else "none")
    return GateResult(
        node_id="3.6",
        answer=triggered,
        branch=branch,
        confidence=0.85,
        evidence={"run_length": run.length, "exhaustion": flags},
        reason=(
            f"衰竭信号{flags} → 高潮{('触发' if triggered else '未触发')}"
            if flags
            else f"{run.length}根趋势棒，无衰竭信号"
        ),
    )


@register("3.7")
def gate_3_7(frame: FrameLike, ctx: GateContext) -> GateResult:
    atr = atr_of(frame, ctx)
    episodes = _count_spike_episodes(frame.bars, atr, lookback=40)
    second = episodes >= 2
    return GateResult(
        node_id="3.7",
        answer="second_or_third" if second else "first",
        branch="second_or_third" if second else "first",
        confidence=0.6,
        evidence={"episodes": episodes},
        reason=f"近40根内识别到{episodes}段尖峰",
    )


def _run_bars(bars: tuple[KlineBar, ...], run: SpikeRun) -> list[KlineBar]:
    selected = [b for b in bars if run.start_seq <= int(b.seq) <= run.end_seq]
    selected.sort(key=lambda b: int(b.seq), reverse=True)  # oldest -> newest
    return selected


def _exhaustion_flags(run_bars: list[KlineBar], direction: str) -> list[str]:
    if len(run_bars) < 3:
        return []
    flags: list[str] = []
    last = run_bars[-1]
    high = max(float(last.high), float(last.low))
    low = min(float(last.high), float(last.low))
    full = high - low
    body = abs(float(last.close) - float(last.open))
    if full > 0 and body > 0:
        upper_wick = (high - max(float(last.open), float(last.close))) / full
        lower_wick = (min(float(last.open), float(last.close)) - low) / full
        wick = upper_wick if direction == "bullish" else lower_wick
        # 规范：尾线 > 实体 × 50%（两者都按全幅归一）
        if wick > (body / full) * Spike.CLIMAX_WICK_TO_BODY_MIN:
            flags.append("long_wick")
    bodies = [abs(float(b.close) - float(b.open)) for b in run_bars]
    mean_body = sum(bodies) / len(bodies)
    if mean_body > 0 and bodies[-1] < mean_body * Spike.CLIMAX_BODY_TO_MEAN_MAX:
        flags.append("small_body")
    if direction == "bullish" and float(last.close) < float(last.open):
        flags.append("opposite_bar")
    if direction == "bearish" and float(last.close) > float(last.open):
        flags.append("opposite_bar")
    return flags


def _count_spike_episodes(
    bars: tuple[KlineBar, ...], atr: float | None, *, lookback: int
) -> int:
    if not bars or not atr or atr <= 0:
        return 0
    chrono = list(reversed(bars[:lookback]))
    count = 0
    i = 0
    while i < len(chrono):
        o, c = float(chrono[i].open), float(chrono[i].close)
        d = 1 if c > o else (-1 if c < o else 0)
        if d == 0 or abs(c - o) < 0.5 * atr:
            i += 1
            continue
        length = 1
        j = i + 1
        while j < len(chrono):
            oj, cj = float(chrono[j].open), float(chrono[j].close)
            dj = 1 if cj > oj else (-1 if cj < oj else 0)
            if dj != d or abs(cj - oj) < 0.35 * atr:
                break
            length += 1
            j += 1
        if length >= 3:
            count += 1
        i = j if j > i + 1 else i + 1
    return count


def _confidence_margin(value: float, threshold: float, *, base: float) -> float:
    if threshold <= 0:
        return base
    margin = abs(value - threshold) / threshold
    return round(min(base, 0.5 + margin), 3)
