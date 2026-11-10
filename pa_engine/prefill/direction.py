"""§2.3 方向预填：五信号投票（从 v1 `judge_direction` 移植）。

信号：EMA 斜率 / 加权收盘重心 / 摆动结构 / 趋势棒占优 / 重叠率。
中窗口（20 根）冲突时：|score|≥4 不扣分（新趋势优先），否则 |score| 减 1。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from pa_engine.config.thresholds import Prefill
from pa_engine.data.types import KlineBar, KlineFrame


@dataclass(frozen=True)
class DirectionResult:
    direction: str  # bullish / bearish / neutral
    score: int
    reason: str
    votes: dict[str, int]


def judge_direction(frame: KlineFrame) -> DirectionResult:
    bars = frame.bars
    ema20 = frame.indicators.ema20
    atr14 = frame.indicators.atr14

    n = max((int(b.seq) for b in bars), default=0) or len(bars)
    W = min(Prefill.DIRECTION_WINDOW, n)
    W_med = min(Prefill.DIRECTION_WINDOW_MED, n)

    closes = [_safe_close(bar) for bar in bars[:W]]
    s1, s1_desc = _signal_ema_slope(ema20, atr14, n)
    s2, s2_desc = _signal_closing_gravity(closes, W, atr14)
    s3, s3_desc = _signal_swing_structure(bars, W)
    s4, s4_desc = _signal_trend_bars(bars, W)
    s5, s5_desc = _signal_overlap(bars, W, s1)

    score = s1 + s2 + s3 + s4 + s5

    med_score = _medium_gravity(bars, W_med, atr14)
    med_desc = f"中窗口重心:{med_score}"
    if med_score != 0 and score != 0 and med_score != (1 if score > 0 else -1):
        if abs(score) >= Prefill.DIRECTION_STRONG_SHORT_SCORE:
            med_desc += f"（冲突但|score|={abs(score)}≥{Prefill.DIRECTION_STRONG_SHORT_SCORE}，新趋势优先，不扣分）"
        else:
            before = score
            score = score - (1 if score > 0 else -1)
            med_desc += f"（冲突，score {before}→{score}）"
    elif med_score != 0:
        med_desc += "（与短窗口一致）"

    if score >= Prefill.DIRECTION_BULL_THRESHOLD:
        direction = "bullish"
    elif score <= Prefill.DIRECTION_BEAR_THRESHOLD:
        direction = "bearish"
    else:
        direction = "neutral"

    reason = (
        f"五信号投票（阈值±{Prefill.DIRECTION_BULL_THRESHOLD}）："
        f"{s1_desc}；{s2_desc}；{s3_desc}；{s4_desc}；{s5_desc}。{med_desc}。"
        f"score={score}→{direction}"
    )
    return DirectionResult(
        direction=direction,
        score=score,
        reason=reason,
        votes={"ema": s1, "gravity": s2, "swing": s3, "trend_bar": s4, "overlap": s5},
    )


def swing_prices(bars: tuple[KlineBar, ...], window: int) -> tuple[list[float], list[float]]:
    """v1 `_find_swings` 等价：左右各 2 根分形，返回价格（新→旧）。"""
    sample = list(bars[:window])
    if len(sample) < 5:
        return [], []
    highs: list[float] = []
    lows: list[float] = []
    for i in range(2, len(sample) - 2):
        h = float(sample[i].high)
        if (
            float(sample[i - 1].high) < h
            and float(sample[i - 2].high) < h
            and float(sample[i + 1].high) < h
            and float(sample[i + 2].high) < h
        ):
            highs.append(h)
        lo = float(sample[i].low)
        if (
            float(sample[i - 1].low) > lo
            and float(sample[i - 2].low) > lo
            and float(sample[i + 1].low) > lo
            and float(sample[i + 2].low) > lo
        ):
            lows.append(lo)
    return highs, lows


def _safe_close(bar: KlineBar) -> float:
    try:
        return float(bar.close)
    except (TypeError, ValueError):
        return math.nan


def _atr0(atr14: tuple[float, ...]) -> float | None:
    if atr14 and not math.isnan(float(atr14[0])):
        return float(atr14[0])
    return None


def _signal_ema_slope(ema20, atr14, n: int) -> tuple[int, str]:
    try:
        if ema20 and not math.isnan(float(ema20[0])):
            k = min(Prefill.EMA_SLOPE_LOOKBACK, n - 1)
            if k >= 1 and len(ema20) > k and not math.isnan(float(ema20[k])):
                d = float(ema20[0]) - float(ema20[k])
                atr = _atr0(atr14)
                thr = Prefill.DIRECTION_EMA_SLOPE_ATR * atr if atr else 0.0
                if d > thr:
                    return 1, f"EMA斜率:+1(d={d:.4f}>thr={thr:.4f})"
                if d < -thr:
                    return -1, f"EMA斜率:-1(d={d:.4f}<-thr={-thr:.4f})"
                return 0, f"EMA斜率:0(d={d:.4f},死区±{thr:.4f})"
    except (TypeError, ValueError):
        pass
    return 0, "EMA斜率:0"


def _signal_closing_gravity(
    closes: list[float], W: int, atr14
) -> tuple[int, str]:
    h = W // 2
    if h < 1 or len(closes) < 2 * h:
        return 0, "收盘重心:0"

    def weighted_avg(vals: list[float], start: int) -> float:
        total_w = total_wv = 0.0
        for i, v in enumerate(vals):
            if math.isnan(v):
                continue
            weight = W - (start + i)
            total_w += weight
            total_wv += weight * v
        return total_wv / total_w if total_w > 0 else math.nan

    near = weighted_avg(closes[:h], 0)
    far = weighted_avg(closes[h : 2 * h], h)
    if math.isnan(near) or math.isnan(far):
        return 0, "收盘重心:0"
    diff = near - far
    atr = _atr0(atr14)
    thr = Prefill.DIRECTION_COG_ATR * atr if atr else 0.0
    if diff > thr:
        return 1, f"收盘重心:+1(diff={diff:.4f}>thr={thr:.4f})"
    if diff < -thr:
        return -1, f"收盘重心:-1(diff={diff:.4f}<-thr={-thr:.4f})"
    return 0, f"收盘重心:0(diff={diff:.4f},死区±{thr:.4f})"


def _signal_swing_structure(bars, W: int) -> tuple[int, str]:
    try:
        highs, lows = swing_prices(bars, W)
        if len(highs) >= 2 and len(lows) >= 2:
            hh = highs[0] > highs[1]
            hl = lows[0] > lows[1]
            ll = lows[0] < lows[1]
            lh = highs[0] < highs[1]
            if hh and hl:
                return 1, "波段结构:+1(HH+HL)"
            if ll and lh:
                return -1, "波段结构:-1(LL+LH)"
            return 0, f"波段结构:0(HH={hh},HL={hl},LL={ll},LH={lh})"
        return 0, f"波段结构:0(枢轴不足 highs={len(highs)},lows={len(lows)})"
    except (TypeError, ValueError, IndexError):
        return 0, "波段结构:0"


def _signal_trend_bars(bars, W: int) -> tuple[int, str]:
    bull = bear = 0
    for bar in list(bars)[:W]:
        high = max(float(bar.high), float(bar.low))
        low = min(float(bar.high), float(bar.low))
        full_range = high - low
        if full_range <= 0:
            continue
        body_ratio = abs(float(bar.close) - float(bar.open)) / full_range
        close_pos = max(0.0, min(1.0, (float(bar.close) - low) / full_range))
        if body_ratio <= 0.25:
            continue
        if float(bar.close) > float(bar.open) and close_pos >= 0.65:
            bull += 1
        elif float(bar.close) < float(bar.open) and close_pos <= 0.35:
            bear += 1
    if bull + bear == 0:
        return 0, "趋势棒占比:0(无趋势棒)"
    ratio = Prefill.TREND_BAR_DOMINANCE_RATIO
    if bull > 0 and bear == 0:
        return 1, f"趋势棒占比:+1(多{bull}/空0)"
    if bear > 0 and bull == 0:
        return -1, f"趋势棒占比:-1(空{bear}/多0)"
    if bull >= bear * ratio:
        return 1, f"趋势棒占比:+1(多{bull}/空{bear}≥{ratio:.1f}×)"
    if bear >= bull * ratio:
        return -1, f"趋势棒占比:-1(空{bear}/多{bull}≥{ratio:.1f}×)"
    return 0, f"趋势棒占比:0(多{bull}/空{bear})"


def _signal_overlap(bars, W: int, ema_slope: int) -> tuple[int, str]:
    ratios: list[float] = []
    window = list(bars)[:W]
    for i in range(len(window) - 1):
        try:
            cur, prv = window[i], window[i + 1]
            cur_h = max(float(cur.high), float(cur.low))
            cur_l = min(float(cur.high), float(cur.low))
            prv_h = max(float(prv.high), float(prv.low))
            prv_l = min(float(prv.high), float(prv.low))
            overlap = max(0.0, min(cur_h, prv_h) - max(cur_l, prv_l))
            union = max(cur_h, prv_h) - min(cur_l, prv_l)
            if union > 0:
                ratios.append(overlap / union)
        except (TypeError, ValueError):
            continue
    if len(ratios) < 2:
        return 0, "K线重叠:0"
    mean_overlap = sum(ratios) / len(ratios)
    if mean_overlap < Prefill.OVERLAP_LOW:
        if ema_slope > 0:
            return 1, f"K线重叠:+1(均值{mean_overlap:.3f}<{Prefill.OVERLAP_LOW})"
        if ema_slope < 0:
            return -1, f"K线重叠:-1(均值{mean_overlap:.3f}<{Prefill.OVERLAP_LOW})"
        return 0, f"K线重叠:0(均值{mean_overlap:.3f}<阈值,但EMA中性)"
    if mean_overlap > Prefill.OVERLAP_HIGH:
        return 0, f"K线重叠:0(均值{mean_overlap:.3f}>{Prefill.OVERLAP_HIGH},区间)"
    return 0, f"K线重叠:0(均值{mean_overlap:.3f},中等)"


def _medium_gravity(bars, W_med: int, atr14) -> int:
    closes = [_safe_close(bar) for bar in list(bars)[:W_med]]
    hm = W_med // 2
    if hm < 1 or len(closes) < 2 * hm:
        return 0

    def weighted(vals: list[float], start: int) -> float:
        tw = tv = 0.0
        for i, v in enumerate(vals):
            if math.isnan(v):
                continue
            weight = W_med - (start + i)
            tw += weight
            tv += weight * v
        return tv / tw if tw > 0 else math.nan

    near = weighted(closes[:hm], 0)
    far = weighted(closes[hm : 2 * hm], hm)
    if math.isnan(near) or math.isnan(far):
        return 0
    diff = near - far
    atr = _atr0(atr14)
    thr = Prefill.DIRECTION_COG_ATR * atr if atr else 0.0
    if diff > thr:
        return 1
    if diff < -thr:
        return -1
    return 0
