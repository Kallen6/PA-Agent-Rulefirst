"""结构级特征（从 v1 `pa_agent/ai/market_features.py` 移植）。

保留 v1 的判定逻辑与阈值，仅做 v2 类型/常量接线；用于闸门与预填。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from pa_engine.config.thresholds import Bar, Structure
from pa_engine.data.types import KlineBar, KlineFrame

_RECENT_BREAKOUT_MAX_SEQ = 12
_MAX_BREAKOUT_EVENTS = 6
_EVENT_PRIORITY = {"failed": 0, "breakout": 1, "test": 2}
_HL_PIVOT_RIGHT = 2
_HL_MAX_LEG_BARS = 30
_SCALE_RECENT = 20
_SCALE_BG_START = 40


@dataclass(frozen=True)
class SwingPivot:
    seq: int
    kind: str
    price: float


@dataclass(frozen=True)
class BreakoutEvent:
    level_price: float
    level_kind: str
    event: str
    trigger_seq: int
    bar_range: str
    note: str
    attempt_type: str = "close"


@dataclass(frozen=True)
class HLCountState:
    bull_count: int
    bear_count: int
    last_bull_trigger_seq: int | None
    last_bear_trigger_seq: int | None
    bull_candidate: str
    bear_candidate: str
    bar_range: str
    bull_leg_start_seq: int | None = None
    bear_leg_start_seq: int | None = None


@dataclass(frozen=True)
class MeasuredMoveCandidate:
    kind: str
    reference: str
    height: float
    target_price: float
    bar_range: str


@dataclass(frozen=True)
class SimpleMarketFeatures:
    lookback_bars: int
    range_high: float | None
    range_low: float | None
    range_width_atr: float | None
    price_position: float | None
    zone: str
    dist_to_high_atr: float | None
    dist_to_low_atr: float | None
    overlap_mean_10: float | None
    doji_inside_ratio_10: float | None
    barbwire_score: float
    barbwire_candidate: bool
    swing_structure: str
    swings: tuple[SwingPivot, ...]
    pullback_depth_atr: float | None
    pullback_bars: int | None
    breakout_events: tuple[BreakoutEvent, ...]
    hl_count: HLCountState
    supports: tuple[float, ...]
    resistances: tuple[float, ...]
    invalidation_long: float | None
    invalidation_short: float | None
    measured_moves: tuple[MeasuredMoveCandidate, ...]
    breakout_quality: str
    breakout_attempt_type: str | None
    mm_as_tp_ok: bool
    spike_aftermath_hint: str
    scale_conflict: bool


def compute_simple_market_features(
    frame: KlineFrame,
    *,
    lookback: int = Structure.LOOKBACK,
) -> SimpleMarketFeatures:
    bars = frame.bars
    n = len(bars)
    if n == 0:
        return _empty_features(lookback)

    window = bars[: min(lookback, n)]
    atr_val = _current_atr(frame)
    close = float(window[0].close)

    range_high, range_low = _range_envelope(window)
    range_width_atr = None
    price_position = None
    zone = "unknown"
    dist_high = None
    dist_low = None

    if range_high is not None and range_low is not None and range_high > range_low:
        width = range_high - range_low
        if atr_val and atr_val > 0:
            range_width_atr = round(width / atr_val, 3)
            dist_high = round((range_high - close) / atr_val, 3)
            dist_low = round((close - range_low) / atr_val, 3)
        price_position = round((close - range_low) / width, 3)
        zone = _zone_from_position(price_position)

    overlap_mean = _mean_overlap_ratio(window, min(Structure.OVERLAP_WINDOW, len(window)))
    doji_ratio = _doji_inside_ratio(window, min(Structure.OVERLAP_WINDOW, len(window)))
    barbwire_score = _barbwire_score(
        overlap_mean, doji_ratio, range_width_atr, window, atr_val
    )

    swings = _find_swings_with_seq(window)
    swing_structure = _label_swing_structure(swings)
    pullback_depth, pullback_bars = _pullback_metrics(window, swings, atr_val, close)

    tick = infer_price_tick(frame) or 0.0
    raw_events = _detect_breakout_events(
        window, range_high, range_low, tick=tick, atr=atr_val
    )
    breakout_events = _dedupe_breakout_events(raw_events)

    hl_count = _compute_hl_count(window)
    supports, resistances = _structure_levels(window, close, swings)
    measured_moves = _measured_move_candidates(
        range_high, range_low, swings, close, lookback=min(lookback, n)
    )
    breakout_quality, breakout_attempt_type = _classify_breakout_quality(
        window, breakout_events, range_high, range_low, atr_val
    )

    return SimpleMarketFeatures(
        lookback_bars=min(lookback, n),
        range_high=range_high,
        range_low=range_low,
        range_width_atr=range_width_atr,
        price_position=price_position,
        zone=zone,
        dist_to_high_atr=dist_high,
        dist_to_low_atr=dist_low,
        overlap_mean_10=overlap_mean,
        doji_inside_ratio_10=doji_ratio,
        barbwire_score=round(barbwire_score, 3),
        barbwire_candidate=barbwire_score >= Structure.BARBWIRE_SCORE_MIN,
        swing_structure=swing_structure,
        swings=tuple(swings),
        pullback_depth_atr=pullback_depth,
        pullback_bars=pullback_bars,
        breakout_events=tuple(breakout_events),
        hl_count=hl_count,
        supports=tuple(supports),
        resistances=tuple(resistances),
        invalidation_long=supports[0] if supports else None,
        invalidation_short=resistances[0] if resistances else None,
        measured_moves=tuple(measured_moves),
        breakout_quality=breakout_quality,
        breakout_attempt_type=breakout_attempt_type,
        mm_as_tp_ok=breakout_quality == "surviving",
        spike_aftermath_hint=_spike_aftermath_hint(window, atr_val),
        scale_conflict=_scale_conflict(window),
    )


# ── helpers shared with gates/prefill ─────────────────────────────────────────


def trend_bar_counts(bars: tuple[KlineBar, ...], window: int) -> tuple[int, int]:
    """Count bull/bear trend bars in first *window* bars (same rule as v1)."""
    bull = 0
    bear = 0
    for bar in list(bars)[:window]:
        high = max(float(bar.high), float(bar.low))
        low = min(float(bar.high), float(bar.low))
        full_range = high - low
        if full_range <= 0:
            continue
        body_ratio = abs(float(bar.close) - float(bar.open)) / full_range
        close_pos = max(0.0, min(1.0, (float(bar.close) - low) / full_range))
        if body_ratio <= Bar.DOJI_BODY_RATIO_MAX:
            continue
        if float(bar.close) > float(bar.open) and close_pos >= Bar.TREND_BULL_CLOSE_POS_MIN:
            bull += 1
        elif float(bar.close) < float(bar.open) and close_pos <= Bar.TREND_BEAR_CLOSE_POS_MAX:
            bear += 1
    return bull, bear


def mean_overlap_ratio(bars: tuple[KlineBar, ...], window: int) -> float | None:
    return _mean_overlap_ratio(bars, window)


def find_confirmed_swings(
    bars: tuple[KlineBar, ...], window: int = Structure.LOOKBACK
) -> list[SwingPivot]:
    """2-bar fractal swings on newest-first bars (used by HL count and structure)."""
    sample = list(bars[:window])
    pivots: list[SwingPivot] = []
    for i in range(2, len(sample) - 2):
        h = float(sample[i].high)
        lo = float(sample[i].low)
        if (
            float(sample[i - 1].high) < h
            and float(sample[i - 2].high) < h
            and float(sample[i + 1].high) < h
            and float(sample[i + 2].high) < h
        ):
            pivots.append(SwingPivot(seq=int(sample[i].seq), kind="high", price=round(h, 6)))
        if (
            float(sample[i - 1].low) > lo
            and float(sample[i - 2].low) > lo
            and float(sample[i + 1].low) > lo
            and float(sample[i + 2].low) > lo
        ):
            pivots.append(SwingPivot(seq=int(sample[i].seq), kind="low", price=round(lo, 6)))
    pivots.sort(key=lambda p: p.seq)
    return pivots


def count_hh_hl_groups(
    bars: tuple[KlineBar, ...], window: int = Structure.LOOKBACK
) -> int:
    """Consecutive HH+HL / LL+LH groups among confirmed swings (v2 gate helper).

    口径：按 seq 升序（旧→新）配对相邻的同向高点/低点，连续满足
    high[i] > high[i-1] 且 low[i] > low[i-1]（多头）或反向（空头）计 1 组。
    """
    pivots = find_confirmed_swings(bars, window)
    # 统一改为「旧→新」，并把相邻同类枢轴压成极值，避免高低点数量不等时错位配对。
    ordered = sorted(pivots, key=lambda p: p.seq, reverse=True)
    cleaned: list[SwingPivot] = []
    for pivot in ordered:
        if cleaned and cleaned[-1].kind == pivot.kind:
            prev = cleaned[-1]
            more_extreme = (
                pivot.price > prev.price if pivot.kind == "high" else pivot.price < prev.price
            )
            if more_extreme:
                cleaned[-1] = pivot
            continue
        cleaned.append(pivot)
    highs = [p for p in cleaned if p.kind == "high"]
    lows = [p for p in cleaned if p.kind == "low"]
    pairs = min(len(highs), len(lows))
    if pairs < 2:
        return 0

    groups = 0
    direction = 0
    for i in range(1, pairs):
        hh = highs[i].price > highs[i - 1].price
        hl = lows[i].price > lows[i - 1].price
        ll = lows[i].price < lows[i - 1].price
        lh = highs[i].price < highs[i - 1].price
        current = 0
        if hh and hl:
            current = 1
        elif ll and lh:
            current = -1
        if current == 0:
            direction = 0
            continue
        if current == direction:
            groups += 1
        else:
            direction = current
            groups = 1
    return groups


def count_trending_tr_groups(
    bars: tuple[KlineBar, ...], window: int = Structure.LOOKBACK
) -> int:
    """trending_tr 判据：HH+HL/LL+LH 的有效组数（最多计 3）。"""
    return min(count_hh_hl_groups(bars, window), 3)


# ── ported v1 helpers ─────────────────────────────────────────────────────────


def _empty_features(lookback: int) -> SimpleMarketFeatures:
    empty_hl = HLCountState(0, 0, None, None, "none", "none", "不适用")
    return SimpleMarketFeatures(
        lookback_bars=0,
        range_high=None,
        range_low=None,
        range_width_atr=None,
        price_position=None,
        zone="unknown",
        dist_to_high_atr=None,
        dist_to_low_atr=None,
        overlap_mean_10=None,
        doji_inside_ratio_10=None,
        barbwire_score=0.0,
        barbwire_candidate=False,
        swing_structure="insufficient",
        swings=(),
        pullback_depth_atr=None,
        pullback_bars=None,
        breakout_events=(),
        hl_count=empty_hl,
        supports=(),
        resistances=(),
        invalidation_long=None,
        invalidation_short=None,
        measured_moves=(),
        breakout_quality="none",
        breakout_attempt_type=None,
        mm_as_tp_ok=False,
        spike_aftermath_hint="none",
        scale_conflict=False,
    )


def _current_atr(frame: KlineFrame) -> float | None:
    atr14 = frame.indicators.atr14
    if not atr14:
        return None
    val = atr14[0]
    if math.isnan(val) or val <= 0:
        return None
    return float(val)


def _range_envelope(bars: tuple[KlineBar, ...]) -> tuple[float | None, float | None]:
    if not bars:
        return None, None
    return max(float(b.high) for b in bars), min(float(b.low) for b in bars)


def _zone_from_position(position: float) -> str:
    if position < 1 / 3:
        return "lower_third"
    if position > 2 / 3:
        return "upper_third"
    return "middle_third"


def _overlap_ratio(bar: KlineBar, prev: KlineBar) -> float | None:
    high = min(float(bar.high), float(prev.high))
    low = max(float(bar.low), float(prev.low))
    overlap = max(0.0, high - low)
    denominator = max(float(bar.high), float(prev.high)) - min(float(bar.low), float(prev.low))
    if denominator <= 0:
        return None
    return overlap / denominator


def _mean_overlap_ratio(bars: tuple[KlineBar, ...], window: int) -> float | None:
    if len(bars) < 2 or window < 2:
        return None
    vals: list[float] = []
    limit = min(window, len(bars) - 1)
    for idx in range(limit):
        ratio = _overlap_ratio(bars[idx], bars[idx + 1])
        if ratio is not None:
            vals.append(ratio)
    if not vals:
        return None
    return round(sum(vals) / len(vals), 3)


def _doji_inside_ratio(bars: tuple[KlineBar, ...], window: int) -> float | None:
    if not bars:
        return None
    sample = bars[:window]
    if not sample:
        return None
    count = 0
    for idx, bar in enumerate(sample):
        prev = sample[idx + 1] if idx + 1 < len(sample) else None
        full_range = max(float(bar.high), float(bar.low)) - min(float(bar.high), float(bar.low))
        body = abs(float(bar.close) - float(bar.open))
        is_doji = full_range > 0 and body / full_range <= Bar.DOJI_BODY_RATIO_MAX
        is_inside = False
        if prev is not None:
            is_inside = float(bar.high) <= float(prev.high) and float(bar.low) >= float(prev.low)
        if is_doji or is_inside:
            count += 1
    return round(count / len(sample), 3)


def _barbwire_score(
    overlap_mean: float | None,
    doji_ratio: float | None,
    range_width_atr: float | None,
    bars: tuple[KlineBar, ...],
    atr: float | None,
) -> float:
    score = 0.0
    if overlap_mean is not None and overlap_mean >= Structure.BARBWIRE_OVERLAP_MIN:
        score += 0.4
    if doji_ratio is not None and doji_ratio >= 0.4:
        score += 0.2
    if range_width_atr is not None and range_width_atr <= Structure.BARBWIRE_WIDTH_ATR_MAX:
        score += 0.2
    if bars and atr and atr > 0:
        avg_bar = sum(float(b.high) - float(b.low) for b in bars[: Structure.OVERLAP_WINDOW]) / min(
            len(bars), Structure.OVERLAP_WINDOW
        )
        rh, rl = _range_envelope(bars[: Structure.OVERLAP_WINDOW])
        if rh is not None and rl is not None and avg_bar > 0:
            if (rh - rl) / avg_bar < 0.3:
                score += 0.2
    return min(score, 1.0)


def _find_swings_with_seq(bars: tuple[KlineBar, ...]) -> list[SwingPivot]:
    if len(bars) < 3:
        return []
    pivots: list[SwingPivot] = []
    for i in range(len(bars)):
        h = float(bars[i].high)
        lo = float(bars[i].low)
        is_high = True
        is_low = True
        if i > 0:
            if h <= float(bars[i - 1].high):
                is_high = False
            if lo >= float(bars[i - 1].low):
                is_low = False
        if i + 1 < len(bars):
            if h <= float(bars[i + 1].high):
                is_high = False
            if lo >= float(bars[i + 1].low):
                is_low = False
        if is_high:
            pivots.append(SwingPivot(seq=int(bars[i].seq), kind="high", price=round(h, 6)))
        if is_low:
            pivots.append(SwingPivot(seq=int(bars[i].seq), kind="low", price=round(lo, 6)))
    pivots.sort(key=lambda p: p.seq)
    return pivots


def _label_swing_structure(swings: list[SwingPivot]) -> str:
    highs = sorted([p for p in swings if p.kind == "high"], key=lambda p: p.seq)
    lows = sorted([p for p in swings if p.kind == "low"], key=lambda p: p.seq)
    if len(highs) < 2 or len(lows) < 2:
        return "insufficient"
    hh = highs[0].price > highs[1].price
    hl = lows[0].price > lows[1].price
    ll = lows[0].price < lows[1].price
    lh = highs[0].price < highs[1].price
    if hh and hl:
        return "HH+HL"
    if ll and lh:
        return "LL+LH"
    return "mixed"


def _pullback_metrics(
    bars: tuple[KlineBar, ...],
    swings: list[SwingPivot],
    atr: float | None,
    close: float,
) -> tuple[float | None, int | None]:
    if not swings or not atr or atr <= 0:
        return None, None
    highs = [p for p in swings if p.kind == "high"]
    lows = [p for p in swings if p.kind == "low"]
    if not highs and not lows:
        return None, None

    if highs and (not lows or highs[-1].seq >= lows[-1].seq):
        pivot_price = highs[-1].price
        depth = max(0.0, pivot_price - close)
    else:
        pivot_price = lows[-1].price
        depth = max(0.0, close - pivot_price)

    pivot_seq = (
        highs[-1].seq if highs and (not lows or highs[-1].seq >= lows[-1].seq) else lows[-1].seq
    )
    bars_since = max(0, pivot_seq - 1)
    return round(depth / atr, 3), bars_since


def _detect_breakout_events(
    bars: tuple[KlineBar, ...],
    range_high: float | None,
    range_low: float | None,
    *,
    tick: float,
    atr: float | None,
) -> list[BreakoutEvent]:
    if not bars:
        return []

    tolerance = (atr or 0.0) * Structure.BREAKOUT_TEST_TOLERANCE_ATR
    if tick <= 0:
        tick = 0.01
    chronological = list(reversed(bars))
    events: list[BreakoutEvent] = []

    running_high: float | None = None
    running_low: float | None = None
    broke_up_seq: int | None = None
    broke_up_level: float | None = None
    broke_down_seq: int | None = None
    broke_down_level: float | None = None

    for bar in chronological:
        seq = int(bar.seq)
        close = float(bar.close)
        low = float(bar.low)
        high = float(bar.high)

        if running_high is not None and running_low is not None:
            if close > running_high + tick:
                broke_up_seq = seq
                broke_up_level = running_high
                events.append(
                    BreakoutEvent(
                        level_price=running_high,
                        level_kind="range_high",
                        event="breakout",
                        trigger_seq=seq,
                        bar_range=f"K{seq}",
                        note="收盘突破当时区间上沿",
                    )
                )
            elif broke_up_seq is not None and broke_up_level is not None:
                bars_after = broke_up_seq - seq if seq < broke_up_seq else 0
                if close < broke_up_level - tick and bars_after <= Structure.BREAKOUT_RECLAIM_BARS:
                    events.append(
                        BreakoutEvent(
                            level_price=broke_up_level,
                            level_kind="range_high",
                            event="failed",
                            trigger_seq=seq,
                            bar_range=f"K{broke_up_seq}-K{seq}",
                            note="突破后收回区间内",
                        )
                    )
                    broke_up_seq = None
                    broke_up_level = None
                elif (
                    low <= broke_up_level + tolerance
                    and close > broke_up_level
                    and bars_after <= Structure.BREAKOUT_RECLAIM_BARS
                ):
                    events.append(
                        BreakoutEvent(
                            level_price=broke_up_level,
                            level_kind="range_high",
                            event="test",
                            trigger_seq=seq,
                            bar_range=f"K{broke_up_seq}-K{seq}",
                            note="回测上沿后仍收上沿之上",
                        )
                    )

            if running_low is not None and close < running_low - tick:
                broke_down_seq = seq
                broke_down_level = running_low
                events.append(
                    BreakoutEvent(
                        level_price=running_low,
                        level_kind="range_low",
                        event="breakout",
                        trigger_seq=seq,
                        bar_range=f"K{seq}",
                        note="收盘跌破当时区间下沿",
                    )
                )
            elif broke_down_seq is not None and broke_down_level is not None:
                bars_after = broke_down_seq - seq if seq < broke_down_seq else 0
                if close > broke_down_level + tick and bars_after <= Structure.BREAKOUT_RECLAIM_BARS:
                    events.append(
                        BreakoutEvent(
                            level_price=broke_down_level,
                            level_kind="range_low",
                            event="failed",
                            trigger_seq=seq,
                            bar_range=f"K{broke_down_seq}-K{seq}",
                            note="跌破后收回区间内",
                        )
                    )
                    broke_down_seq = None
                    broke_down_level = None
                elif (
                    high >= broke_down_level - tolerance
                    and close < broke_down_level
                    and bars_after <= Structure.BREAKOUT_RECLAIM_BARS
                ):
                    events.append(
                        BreakoutEvent(
                            level_price=broke_down_level,
                            level_kind="range_low",
                            event="test",
                            trigger_seq=seq,
                            bar_range=f"K{broke_down_seq}-K{seq}",
                            note="回测下沿后仍收下沿之下",
                        )
                    )

        running_high = high if running_high is None else max(running_high, high)
        running_low = low if running_low is None else min(running_low, low)

    return events


def _dedupe_breakout_events(events: list[BreakoutEvent]) -> list[BreakoutEvent]:
    if not events:
        return []
    recent = [e for e in events if e.trigger_seq <= _RECENT_BREAKOUT_MAX_SEQ]
    pool = recent if recent else list(events[-_MAX_BREAKOUT_EVENTS:])
    by_level: dict[tuple[str, float], BreakoutEvent] = {}
    for event in pool:
        key = (event.level_kind, round(event.level_price, 6))
        prev = by_level.get(key)
        if prev is None:
            by_level[key] = event
            continue
        if event.trigger_seq < prev.trigger_seq:
            winner = event
        elif event.trigger_seq > prev.trigger_seq:
            winner = prev
        elif _EVENT_PRIORITY[event.event] < _EVENT_PRIORITY[prev.event]:
            winner = event
        else:
            winner = prev
        by_level[key] = winner
    return sorted(by_level.values(), key=lambda e: e.trigger_seq)[:_MAX_BREAKOUT_EVENTS]


def _classify_breakout_quality(
    bars: tuple[KlineBar, ...],
    events: tuple[BreakoutEvent, ...] | list[BreakoutEvent],
    range_high: float | None,
    range_low: float | None,
    atr: float | None,
) -> tuple[str, str | None]:
    if not bars:
        return "none", None

    close0 = float(bars[0].close)
    high0 = float(bars[0].high)
    low0 = float(bars[0].low)
    tick_slack = (atr or 0.0) * 0.05

    if range_high is not None and high0 > range_high + tick_slack and close0 <= range_high:
        wick_only = True
    elif range_low is not None and low0 < range_low - tick_slack and close0 >= range_low:
        wick_only = True
    else:
        wick_only = False

    if not events:
        return ("wick_probe", "wick") if wick_only else ("none", None)

    ordered = sorted(events, key=lambda e: e.trigger_seq)
    latest = ordered[0]
    attempt = getattr(latest, "attempt_type", "close") or "close"

    if latest.event == "failed":
        return "failed", attempt
    if latest.event == "test":
        return "surviving", attempt
    if latest.event == "breakout":
        age = latest.trigger_seq
        later_fail = any(
            e.event == "failed"
            and e.level_kind == latest.level_kind
            and e.trigger_seq < latest.trigger_seq
            for e in events
        )
        if later_fail:
            return "failed", attempt
        outside = False
        if latest.level_kind == "range_high" and close0 > latest.level_price:
            outside = True
        if latest.level_kind == "range_low" and close0 < latest.level_price:
            outside = True
        if not outside:
            return "failed", attempt
        if age >= Structure.SURVIVING_MIN_BARS:
            return "surviving", attempt
        return "close_breakout", attempt

    return ("wick_probe", "wick") if wick_only else ("none", None)


def _spike_aftermath_hint(bars: tuple[KlineBar, ...], atr: float | None) -> str:
    n = min(len(bars), Structure.SPIKE_LOOKBACK)
    if n < Structure.SPIKE_MIN_RUN + 2 or not atr or atr <= 0:
        return "none"

    window = bars[:n]
    chrono = list(reversed(window))
    best = None
    i = 0
    while i < len(chrono):
        o, c = float(chrono[i].open), float(chrono[i].close)
        body = abs(c - o)
        d = 1 if c > o else (-1 if c < o else 0)
        if d == 0 or body < 0.5 * atr:
            i += 1
            continue
        run = [i]
        j = i + 1
        while j < len(chrono):
            oj, cj = float(chrono[j].open), float(chrono[j].close)
            dj = 1 if cj > oj else (-1 if cj < oj else 0)
            if dj != d or abs(cj - oj) < 0.35 * atr:
                break
            run.append(j)
            j += 1
        if len(run) >= Structure.SPIKE_MIN_RUN:
            best = (run[0], run[-1], d)
        i = j if j > i + 1 else i + 1

    if best is None:
        return "none"

    start_i, end_i, d = best
    if d > 0:
        origin = float(chrono[start_i].low)
        spike_end = float(chrono[end_i].close)
        move = max(spike_end - float(chrono[start_i].open), atr)
    else:
        origin = float(chrono[start_i].high)
        spike_end = float(chrono[end_i].close)
        move = max(float(chrono[start_i].open) - spike_end, atr)

    after = chrono[end_i + 1 :]
    if not after:
        return "continued"

    cont = 0
    for bar in after[:4]:
        o, c = float(bar.open), float(bar.close)
        dj = 1 if c > o else (-1 if c < o else 0)
        if dj == d and abs(c - o) >= 0.45 * atr:
            cont += 1
        else:
            break
    if cont >= 2:
        return "continued"

    last_c = float(after[-1].close)
    if d > 0:
        mae = max(0.0, spike_end - min(float(b.low) for b in after))
        origin_break_close = any(float(b.close) < origin for b in after)
        still_wrong = last_c < origin
    else:
        mae = max(0.0, max(float(b.high) for b in after) - spike_end)
        origin_break_close = any(float(b.close) > origin for b in after)
        still_wrong = last_c > origin

    if origin_break_close and still_wrong and mae >= 0.85 * move:
        return "sticky_reversal_risk"
    if origin_break_close or mae >= 1.0 * move:
        return "origin_break_risk"
    if mae >= 0.25 * move:
        return "pullback"
    return "continued"


def _scale_conflict(bars: tuple[KlineBar, ...]) -> bool:
    n = len(bars)
    if n < _SCALE_BG_START + 10:
        return False
    recent = bars[:_SCALE_RECENT]
    bg = bars[_SCALE_BG_START : min(n, _SCALE_BG_START + 40)]
    if len(bg) < 10:
        return False
    r_net = float(recent[0].close) - float(recent[-1].close)
    b_net = float(bg[0].close) - float(bg[-1].close)
    if abs(r_net) < 1e-12 or abs(b_net) < 1e-12:
        return False
    return (r_net > 0) != (b_net > 0)


def _count_pullback_triggers(
    highs: list[float], lows: list[float], seqs: list[int]
) -> tuple[int, int | None, int | None]:
    n = len(highs)
    state: dict | None = None
    for k in range(n):
        s = k - _HL_PIVOT_RIGHT
        if (
            state is None
            and s >= 2
            and highs[s] > highs[s - 1]
            and highs[s] > highs[s - 2]
            and highs[s] > highs[s + 1]
            and highs[s] > highs[k]
        ):
            state = {"s": s, "sh": highs[s], "count": 0, "armed": True, "sig_low": None, "last": None}
        if state is None:
            continue
        if k > state["s"] + _HL_PIVOT_RIGHT and highs[k] > state["sh"]:
            state = None
            continue
        if k - state["s"] > _HL_MAX_LEG_BARS:
            state = None
            continue
        if not state["armed"] and state["sig_low"] is not None and lows[k] < state["sig_low"]:
            state["armed"] = True
        if state["armed"] and k > state["s"] + _HL_PIVOT_RIGHT and highs[k] > highs[k - 1]:
            state["count"] += 1
            state["last"] = seqs[k]
            state["armed"] = False
            state["sig_low"] = lows[k - 1]
    if state is None:
        return 0, None, None
    return state["count"], state["last"], seqs[state["s"]]


def _compute_hl_count(bars: tuple[KlineBar, ...]) -> HLCountState:
    if len(bars) < 6:
        return HLCountState(0, 0, None, None, "none", "none", "不适用")

    chrono = list(reversed(bars))
    highs = [float(b.high) for b in chrono]
    lows = [float(b.low) for b in chrono]
    seqs = [int(b.seq) for b in chrono]

    bull, last_bull, bull_start = _count_pullback_triggers(highs, lows, seqs)
    bear, last_bear, bear_start = _count_pullback_triggers(
        [-x for x in lows], [-x for x in highs], seqs
    )

    def _tag(count: int, prefix: str) -> str:
        if count <= 0:
            return "none"
        return f"{prefix}{min(count, 3)}"

    newest = int(bars[0].seq)
    oldest = int(bars[-1].seq)
    return HLCountState(
        bull_count=bull,
        bear_count=bear,
        last_bull_trigger_seq=last_bull,
        last_bear_trigger_seq=last_bear,
        bull_candidate=_tag(bull, "h"),
        bear_candidate=_tag(bear, "l"),
        bar_range=f"K{newest}-K{oldest}" if newest != oldest else f"K{newest}",
        bull_leg_start_seq=bull_start,
        bear_leg_start_seq=bear_start,
    )


def _structure_levels(
    bars: tuple[KlineBar, ...], close: float, swings: list[SwingPivot]
) -> tuple[list[float], list[float]]:
    supports: list[float] = []
    resistances: list[float] = []
    seen: set[float] = set()
    for pivot in reversed(swings):
        key = round(pivot.price, 8)
        if key in seen:
            continue
        seen.add(key)
        if pivot.kind == "low" and pivot.price < close:
            supports.append(pivot.price)
        elif pivot.kind == "high" and pivot.price > close:
            resistances.append(pivot.price)
    supports.sort(reverse=True)
    resistances.sort()
    return supports[:3], resistances[:3]


def _measured_move_candidates(
    range_high: float | None,
    range_low: float | None,
    swings: list[SwingPivot],
    close: float,
    *,
    lookback: int,
) -> list[MeasuredMoveCandidate]:
    out: list[MeasuredMoveCandidate] = []
    if range_high is not None and range_low is not None and range_high > range_low:
        height = round(range_high - range_low, 6)
        out.append(
            MeasuredMoveCandidate(
                kind="range_up",
                reference="区间高度向上翻测",
                height=height,
                target_price=round(range_high + height, 6),
                bar_range=f"K{lookback}-K1",
            )
        )
        out.append(
            MeasuredMoveCandidate(
                kind="range_down",
                reference="区间高度向下翻测",
                height=height,
                target_price=round(range_low - height, 6),
                bar_range=f"K{lookback}-K1",
            )
        )

    highs = sorted([p for p in swings if p.kind == "high"], key=lambda p: p.seq)
    lows = sorted([p for p in swings if p.kind == "low"], key=lambda p: p.seq)
    if len(highs) >= 1 and len(lows) >= 1:
        leg_up = round(highs[0].price - lows[0].price, 6)
        if leg_up > 0:
            out.append(
                MeasuredMoveCandidate(
                    kind="leg_up",
                    reference=f"最近leg K{lows[0].seq}-K{highs[0].seq}",
                    height=leg_up,
                    target_price=round(close + leg_up, 6),
                    bar_range=f"K{highs[0].seq}-K{lows[0].seq}",
                )
            )
            out.append(
                MeasuredMoveCandidate(
                    kind="leg_down",
                    reference=f"最近leg K{highs[0].seq}-K{lows[0].seq}",
                    height=leg_up,
                    target_price=round(close - leg_up, 6),
                    bar_range=f"K{highs[0].seq}-K{lows[0].seq}",
                )
            )
    return out


def infer_price_tick(frame: KlineFrame) -> float | None:
    """Guess one tick from decimal places in the snapshot（与 v1 一致）。"""
    bars = frame.bars
    if not bars:
        return None
    max_decimals = 0
    for bar in bars:
        for attr in ("open", "high", "low", "close"):
            try:
                value = float(getattr(bar, attr))
            except (TypeError, ValueError):
                continue
            text = f"{value:.12f}".rstrip("0")
            if "." in text:
                max_decimals = max(max_decimals, len(text.split(".")[1]))
    if max_decimals <= 0:
        return 1.0
    return 10 ** (-min(max_decimals, 6))
