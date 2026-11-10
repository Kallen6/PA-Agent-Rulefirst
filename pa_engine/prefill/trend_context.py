"""三窗口背景（从 v1 `trend_context.py` 移植）。

窗口：长程背景 K{n}–K41、近期结构 K40–K1、即时 K8–K1。
输出：background_direction / trading_direction / conflict / recent_spike。
"""
from __future__ import annotations

import math
from typing import Any

from pa_engine.config.thresholds import Prefill
from pa_engine.data.types import KlineFrame
from pa_engine.prefill.direction import swing_prices

BACKGROUND_BAR_START_IDX = 40
RECENT_STRUCT_BARS = 40
SPIKE_NEAR_WINDOW = 8
SPIKE_MIN_TREND_BARS = 3
SPIKE_OVERLAP_MAX = 0.35


def build_trend_context(frame: KlineFrame, trading_direction: str) -> dict[str, Any]:
    bg = compute_background_direction(frame)
    spike = detect_recent_spike(frame)
    td = trading_direction if trading_direction in ("bullish", "bearish", "neutral") else "neutral"
    conflict = (
        td in ("bullish", "bearish") and bg in ("bullish", "bearish") and td != bg
    )
    if td == bg and td != "neutral":
        relationship = "aligned"
    elif conflict:
        relationship = "conflict"
    elif bg == "neutral":
        relationship = "neutral_background"
    else:
        relationship = "mixed"
    return {
        "background_direction": bg,
        "trading_direction": td,
        "primary_direction": td,
        "conflict": conflict,
        "relationship": relationship,
        "recent_spike": spike,
    }


def compute_background_direction(frame: KlineFrame) -> str:
    bars = frame.bars
    n = _bar_count(frame)
    if n <= BACKGROUND_BAR_START_IDX + 8:
        return "neutral"
    bg_bars = bars[BACKGROUND_BAR_START_IDX:]
    ema20 = frame.indicators.ema20
    ema_bg = ema20[BACKGROUND_BAR_START_IDX:] if len(ema20) > BACKGROUND_BAR_START_IDX else ()
    atr = _atr0(frame)
    W = min(30, len(bg_bars))
    score, _ = _direction_vote_on_slice(
        bg_bars, ema_bg, atr, window=W, slope_lookback=min(Prefill.EMA_SLOPE_LOOKBACK, max(W - 1, 1))
    )
    return _score_to_direction(score)


def detect_recent_spike(frame: KlineFrame) -> str | None:
    bars = frame.bars
    n = _bar_count(frame)
    W = min(SPIKE_NEAR_WINDOW, n)
    if W < SPIKE_MIN_TREND_BARS + 1:
        return None
    bull, bear = _trend_bars(bars, W)
    overlap = _mean_overlap(bars, W)
    if overlap is None or overlap > SPIKE_OVERLAP_MAX:
        return None
    score, _ = _direction_vote_on_slice(
        tuple(bars), frame.indicators.ema20, _atr0(frame), window=W, slope_lookback=min(5, max(n - 1, 1))
    )
    ratio = Prefill.TREND_BAR_DOMINANCE_RATIO
    if bull >= SPIKE_MIN_TREND_BARS and bull >= bear * ratio and score >= 2:
        return "bullish"
    if bear >= SPIKE_MIN_TREND_BARS and bear >= bull * ratio and score <= -2:
        return "bearish"
    return None


def render_three_window_summary(frame: KlineFrame, ctx: dict[str, Any]) -> str:
    n = _bar_count(frame)
    return (
        f"三窗口：背景 K{n}–K41 {ctx['background_direction']}；"
        f"近期 K40–K1 {ctx['trading_direction']}（关系 {ctx['relationship']}）；"
        f"即时 K8–K1 " + (f"尖峰 {ctx['recent_spike']}" if ctx.get("recent_spike") else "无尖峰")
    )


def _bar_count(frame: KlineFrame) -> int:
    try:
        return max(int(getattr(b, "seq", 0)) for b in frame.bars)
    except (TypeError, ValueError):
        return len(frame.bars)


def _atr0(frame: KlineFrame) -> float | None:
    atr14 = frame.indicators.atr14
    if atr14 and not math.isnan(float(atr14[0])):
        return float(atr14[0])
    return None


def _score_to_direction(score: int) -> str:
    if score >= Prefill.DIRECTION_BULL_THRESHOLD:
        return "bullish"
    if score <= Prefill.DIRECTION_BEAR_THRESHOLD:
        return "bearish"
    return "neutral"


def _trend_bars(bars, window: int) -> tuple[int, int]:
    bull = bear = 0
    for bar in list(bars)[:window]:
        high = max(float(bar.high), float(bar.low))
        low = min(float(bar.high), float(bar.low))
        full = high - low
        if full <= 0:
            continue
        body_ratio = abs(float(bar.close) - float(bar.open)) / full
        pos = max(0.0, min(1.0, (float(bar.close) - low) / full))
        if body_ratio <= 0.25:
            continue
        if float(bar.close) > float(bar.open) and pos >= 0.65:
            bull += 1
        elif float(bar.close) < float(bar.open) and pos <= 0.35:
            bear += 1
    return bull, bear


def _mean_overlap(bars, window: int) -> float | None:
    vals = []
    sample = list(bars)[:window]
    for i in range(len(sample) - 1):
        cur, prv = sample[i], sample[i + 1]
        ch, cl = max(float(cur.high), float(cur.low)), min(float(cur.high), float(cur.low))
        ph, pl = max(float(prv.high), float(prv.low)), min(float(prv.high), float(prv.low))
        overlap = max(0.0, min(ch, ph) - max(cl, pl))
        union = max(ch, ph) - min(cl, pl)
        if union > 0:
            vals.append(overlap / union)
    if len(vals) < 2:
        return None
    return sum(vals) / len(vals)


def _direction_vote_on_slice(
    bars_slice: tuple,
    ema_slice: tuple[float, ...],
    atr_val: float | None,
    *,
    window: int,
    slope_lookback: int,
) -> tuple[int, str]:
    n = len(bars_slice)
    if n < 5:
        return 0, "数据不足"
    W = min(window, n)
    closes = []
    for bar in list(bars_slice)[:W]:
        try:
            closes.append(float(bar.close))
        except (TypeError, ValueError):
            closes.append(math.nan)

    s1 = 0
    try:
        if ema_slice and not math.isnan(float(ema_slice[0])):
            k = min(slope_lookback, n - 1)
            if k >= 1 and len(ema_slice) > k and not math.isnan(float(ema_slice[k])):
                d = float(ema_slice[0]) - float(ema_slice[k])
                thr = 0.05 * atr_val if atr_val and atr_val > 0 else 0.0
                if d > thr:
                    s1 = 1
                elif d < -thr:
                    s1 = -1
    except (TypeError, ValueError):
        pass

    bull, bear = _trend_bars(bars_slice, W)
    overlap = _mean_overlap(bars_slice, W)

    s2 = 0
    h = W // 2
    if h >= 1 and len(closes) >= 2 * h:
        def wavg(vals, start):
            tw = tv = 0.0
            for i, v in enumerate(vals):
                if math.isnan(v):
                    continue
                weight = W - (start + i)
                tw += weight
                tv += weight * v
            return tv / tw if tw > 0 else math.nan

        near = wavg(closes[:h], 0)
        far = wavg(closes[h : 2 * h], h)
        if not math.isnan(near) and not math.isnan(far):
            diff = near - far
            thr = 0.1 * atr_val if atr_val and atr_val > 0 else 0.0
            if diff > thr:
                s2 = 1
            elif diff < -thr:
                s2 = -1

    s3 = 0
    try:
        highs, lows = swing_prices(bars_slice, W)
        if len(highs) >= 2 and len(lows) >= 2:
            if highs[0] > highs[1] and lows[0] > lows[1]:
                s3 = 1
            elif lows[0] < lows[1] and highs[0] < highs[1]:
                s3 = -1
    except (TypeError, ValueError, IndexError):
        pass

    s4 = 0
    if bull + bear > 0:
        if bull >= bear * Prefill.TREND_BAR_DOMINANCE_RATIO:
            s4 = 1
        elif bear >= bull * Prefill.TREND_BAR_DOMINANCE_RATIO:
            s4 = -1

    s5 = 0
    if overlap is not None and overlap < Prefill.OVERLAP_LOW:
        if s1 > 0:
            s5 = 1
        elif s1 < 0:
            s5 = -1

    score = s1 + s2 + s3 + s4 + s5
    return score, f"score={score}"
