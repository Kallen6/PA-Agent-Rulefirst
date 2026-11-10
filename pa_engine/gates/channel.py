"""§5 通道宽度闸门（A 类，代码判定）。

§5.1 微型通道 / §5.2 紧凑 / §5.3 常规 / §5.4 宽通道 / §5.5 收缩台阶
主判据：最近一波回撤深度（thresholds.Channel），辅助：重叠率、趋势棒占比、HH+HL 组数。
"""
from __future__ import annotations

from pa_engine.config.thresholds import Channel, Spike, Structure
from pa_engine.gates.base import (
    GateContext,
    GateResult,
    FrameLike,
    atr_of,
    register,
    structure_of,
)
from pa_engine.gates.common import (
    detect_last_spike_run,
    latest_direction,
    leg_pullback_pct,
    swing_groups,
    trend_ratio,
)


@register("5.1")
def gate_5_1(frame: FrameLike, ctx: GateContext) -> GateResult:
    atr = atr_of(frame, ctx)
    run = detect_last_spike_run(frame.bars, atr, lookback=Structure.SPIKE_LOOKBACK)
    direction = _direction(frame, ctx)
    pct = leg_pullback_pct(frame.bars, direction)
    if run is None:
        return GateResult("5.1", False, "none", 0.75, {"pullback_pct": pct}, "无连续推进run")
    spike_qualifies = run.body_overlap <= (
        Spike.OVERLAP_MAX_CANDIDATE if run.length == 2 else Spike.OVERLAP_MAX_STANDARD
    )
    micro = (
        2 <= run.length <= 10
        and not spike_qualifies
        and pct is not None
        and pct <= Channel.MICRO_PULLBACK_MAX
    )
    conf = 0.85
    if pct is not None:
        conf = _margin_conf(pct, Channel.MICRO_PULLBACK_MAX, base=0.85)
    if spike_qualifies:
        conf = 0.8
    return GateResult(
        node_id="5.1",
        answer=micro,
        branch="micro_channel" if micro else "no",
        confidence=conf,
        evidence={
            "run_length": run.length,
            "body_overlap": run.body_overlap,
            "spike_qualifies": spike_qualifies,
            "pullback_pct": pct,
        },
        reason=(
            f"{run.length}根连续推进、重叠{run.body_overlap:.0%}、回撤{pct}"
            f"→{'微型通道' if micro else '非微型通道'}"
        ),
    )


@register("5.2")
def gate_5_2(frame: FrameLike, ctx: GateContext) -> GateResult:
    pct, overlap, bull, bear, ratio = _channel_metrics(frame, ctx)
    pct_ok = pct is not None and pct <= Channel.TIGHT_PULLBACK_MAX
    overlap_ok = overlap is not None and overlap < Channel.TIGHT_OVERLAP_MAX
    ratio_ok = ratio >= Channel.TIGHT_TREND_BAR_RATIO_MIN
    tight = pct_ok and overlap_ok and ratio_ok
    if tight:
        conf = min(
            _margin_conf(pct, Channel.TIGHT_PULLBACK_MAX, base=0.9),
            _margin_conf(overlap, Channel.TIGHT_OVERLAP_MAX, base=0.9),
        )
    elif pct_ok:
        # 回撤达标但辅助判据不达标：若某个失败项仍贴着阈值，则是真边界；
        # 若全部远离阈值，则是清晰的「非紧凑」，不应进灰区。
        near = (
            _is_near(overlap, Channel.TIGHT_OVERLAP_MAX, 0.15)
            or _is_near(ratio, Channel.TIGHT_TREND_BAR_RATIO_MIN, 0.15)
        )
        conf = 0.6 if near else 0.85
    else:
        conf = _margin_conf(pct, Channel.TIGHT_PULLBACK_MAX, base=0.85)
    return GateResult(
        node_id="5.2",
        answer=tight,
        branch="tight_channel" if tight else ("borderline" if pct_ok else "no"),
        confidence=conf,
        evidence={
            "pullback_pct": pct,
            "overlap_mean": overlap,
            "trend_bar_ratio": ratio,
            "bull_trend_bars": bull,
            "bear_trend_bars": bear,
            "criteria": {"pullback": pct_ok, "overlap": overlap_ok, "trend_bar": ratio_ok},
        },
        reason=(
            f"回撤{pct:.1%}（≤{Channel.TIGHT_PULLBACK_MAX:.0%}？）"
            f"+ 重叠{overlap}（<{Channel.TIGHT_OVERLAP_MAX:.0%}？）"
            f"+ 趋势棒{ratio:.0%}（≥{Channel.TIGHT_TREND_BAR_RATIO_MIN:.0%}？）"
            f"→{'紧凑通道' if tight else '非紧凑'}"
            if pct is not None
            else "回撤数据不足"
        ),
    )


@register("5.3")
def gate_5_3(frame: FrameLike, ctx: GateContext) -> GateResult:
    pct, overlap, *_ = _channel_metrics(frame, ctx)
    normal = pct is not None and Channel.NORMAL_PULLBACK_MIN < pct <= Channel.NORMAL_PULLBACK_MAX
    conf = 0.85
    if pct is not None:
        margin = min(
            abs(pct - Channel.NORMAL_PULLBACK_MIN), abs(pct - Channel.NORMAL_PULLBACK_MAX)
        ) / max(Channel.NORMAL_PULLBACK_MAX, 1e-9)
        conf = round(min(0.85, 0.55 + margin), 3)
    return GateResult(
        node_id="5.3",
        answer=normal,
        branch="normal_channel" if normal else "no",
        confidence=conf,
        evidence={"pullback_pct": pct, "overlap_mean": overlap},
        reason=(
            f"回撤{pct:.1%}（{Channel.NORMAL_PULLBACK_MIN:.0%}–{Channel.NORMAL_PULLBACK_MAX:.0%}）"
            f"→{'常规通道' if normal else '非常规'}"
            if pct is not None
            else "回撤数据不足"
        ),
    )


@register("5.4")
def gate_5_4(frame: FrameLike, ctx: GateContext) -> GateResult:
    pct, overlap, *_ = _channel_metrics(frame, ctx)
    groups, groups_dir = swing_groups(frame.bars, Structure.LOOKBACK)
    in_band = pct is not None and Channel.BROAD_PULLBACK_MIN < pct <= Channel.BROAD_PULLBACK_MAX
    overlap_ok = overlap is not None and overlap >= Channel.BROAD_OVERLAP_MIN
    groups_ok = groups >= Channel.BROAD_MIN_HHHL_GROUPS
    broad = in_band and overlap_ok and groups_ok
    if in_band and groups <= Channel.TRENDING_TR_MAX_HHHL_GROUPS:
        branch = "trending_tr"
    elif broad:
        branch = "broad_channel"
    elif not in_band:
        branch = "no"
    elif not overlap_ok:
        branch = "insufficient_overlap"
    else:
        branch = "insufficient_groups"
    if branch in ("broad_channel", "trending_tr"):
        conf = 0.8
    elif not in_band:
        # 回撤深度明确不在 50%–78.6% 区间，属清晰否定
        conf = 0.85
    else:
        conf = 0.55
    return GateResult(
        node_id="5.4",
        answer=broad,
        branch=branch,
        confidence=conf,
        evidence={
            "pullback_pct": pct,
            "overlap_mean": overlap,
            "hh_hl_groups": groups,
            "groups_direction": groups_dir,
            "criteria": {"band": in_band, "overlap": overlap_ok, "groups": groups_ok},
        },
        reason=(
            f"回撤{pct:.1%} + 重叠{overlap} + {groups}组HH+HL → {branch}"
            if pct is not None
            else "回撤数据不足"
        ),
    )


@register("5.5")
def gate_5_5(frame: FrameLike, ctx: GateContext) -> GateResult:
    from pa_engine.features.structure import find_confirmed_swings

    pivots = find_confirmed_swings(frame.bars, 60)
    highs = [p for p in pivots if p.kind == "high"][:3]
    lows = [p for p in pivots if p.kind == "low"][:3]
    shrinking = False
    if len(highs) >= 3 and len(lows) >= 3:
        h_rising = highs[0].price > highs[1].price > highs[2].price
        l_rising = lows[0].price > lows[1].price > lows[2].price
        last_push = highs[0].price - lows[0].price
        prev_push = highs[1].price - lows[1].price
        shrinking = h_rising and l_rising and last_push < prev_push
    return GateResult(
        node_id="5.5",
        answer=shrinking,
        branch="staircase_shrinking" if shrinking else "no",
        confidence=0.55,
        evidence={"highs": [p.price for p in highs], "lows": [p.price for p in lows]},
        reason="三段推进且幅度递减" if shrinking else "未形成收缩台阶",
    )


def _direction(frame: FrameLike, ctx: GateContext) -> str:
    direction = ctx.extras.get("direction")
    if direction in ("bullish", "bearish"):
        return direction
    sf = structure_of(frame, ctx)
    if sf.swing_structure == "LL+LH":
        return "bearish"
    if sf.swing_structure == "HH+HL":
        return "bullish"
    return latest_direction(frame.bars, 8)


def _channel_metrics(frame: FrameLike, ctx: GateContext):
    sf = structure_of(frame, ctx)
    direction = _direction(frame, ctx)
    pct = leg_pullback_pct(frame.bars, direction)
    overlap = sf.overlap_mean_10
    bull, bear, ratio = trend_ratio(frame.bars, 10)
    return pct, overlap, bull, bear, ratio


def _margin_conf(value: float | None, threshold: float, *, base: float) -> float:
    if value is None or threshold <= 0:
        return 0.4
    margin = abs(value - threshold) / threshold
    # 余量 8% 以内视为贴近阈值（置信度 <0.8 → 灰区）；余量越大越确定
    return round(min(base, 0.70 + 1.2 * margin), 3)


def _is_near(value: float | None, threshold: float, band: float) -> bool:
    if value is None or threshold <= 0:
        return True
    return abs(value - threshold) / threshold <= band
