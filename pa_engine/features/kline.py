"""单根 K 线几何特征（从 v1 `pa_agent/ai/kline_features.py` 移植）。

与 v1 保持完全一致的判定顺序与阈值来源 `pa_engine.config.thresholds.Bar`。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from pa_engine.config.thresholds import Bar
from pa_engine.data.types import KlineBar, KlineFrame


def bar_candle_direction_label(bar: KlineBar) -> str:
    if bar.close > bar.open:
        return "阳线"
    if bar.close < bar.open:
        return "阴线"
    return "平"


@dataclass(frozen=True)
class KlineGeometryFeature:
    seq: int
    bar_type: str
    body_ratio: float | None
    upper_wick_ratio: float | None
    lower_wick_ratio: float | None
    close_position: float | None
    range_atr_ratio: float | None
    ema_relation: str
    overlap_prev_ratio: float | None
    inside_sequence: str
    ioi_pattern: bool
    micro_double: str
    gap_bar: str
    ema_gap_count: int
    breakout_prev: str
    follow_through_1_2: str
    volume_ratio_20: float | None
    volume_state: str


def compute_kline_geometry_features(
    frame: KlineFrame,
    *,
    limit: int | None = None,
) -> list[KlineGeometryFeature]:
    bars = frame.bars
    features: list[KlineGeometryFeature] = []
    for idx, bar in enumerate(bars):
        atr = frame.indicators.atr14[idx] if idx < len(frame.indicators.atr14) else math.nan
        ema = frame.indicators.ema20[idx] if idx < len(frame.indicators.ema20) else math.nan
        prev = bars[idx + 1] if idx + 1 < len(bars) else None
        prev2 = bars[idx + 2] if idx + 2 < len(bars) else None
        prev3 = bars[idx + 3] if idx + 3 < len(bars) else None
        features.append(
            _feature_for_bar(
                bar,
                prev,
                prev2,
                prev3,
                bars=bars,
                idx=idx,
                atr=atr,
                ema=ema,
                ema_values=frame.indicators.ema20,
            )
        )
    if limit is not None:
        return features[:limit]
    return features


def _feature_for_bar(
    bar: KlineBar,
    prev: KlineBar | None,
    prev2: KlineBar | None,
    prev3: KlineBar | None,
    *,
    bars: tuple[KlineBar, ...],
    idx: int,
    atr: float,
    ema: float,
    ema_values: tuple[float, ...],
) -> KlineGeometryFeature:
    high = max(bar.high, bar.low)
    low = min(bar.high, bar.low)
    open_ = bar.open
    close = bar.close
    full_range = high - low
    body = abs(close - open_)

    if full_range > 0:
        body_ratio = body / full_range
        upper_wick_ratio = (high - max(open_, close)) / full_range
        lower_wick_ratio = (min(open_, close) - low) / full_range
        close_position = max(0.0, min(1.0, (close - low) / full_range))
    else:
        body_ratio = None
        upper_wick_ratio = None
        lower_wick_ratio = None
        close_position = None

    range_atr_ratio = None
    if full_range > 0 and not math.isnan(atr) and atr > 0:
        range_atr_ratio = full_range / atr

    ema_relation = "unknown"
    if not math.isnan(ema):
        if close > ema:
            ema_relation = "above"
        elif close < ema:
            ema_relation = "below"
        else:
            ema_relation = "touch"

    overlap_prev_ratio = _overlap_ratio(bar, prev)
    bar_type = _classify_bar(bar, prev, body_ratio, close_position)
    inside_sequence = _inside_sequence(bar, prev, prev2, prev3)
    ioi_pattern = _is_ioi(bar, prev, prev2, prev3)
    micro_double = _micro_double(bar, prev, atr=atr)
    gap_bar = _gap_bar(bar, ema)
    ema_gap_count = _ema_gap_count(bars, idx, ema_values)
    breakout_prev = _breakout_prev_range(bars, idx)
    follow_through_1_2 = _follow_through_1_2(bars, idx)
    vol_ratio, vol_state = _volume_ratio_state(bars, idx)

    return KlineGeometryFeature(
        seq=bar.seq,
        bar_type=bar_type,
        body_ratio=_round_or_none(body_ratio),
        upper_wick_ratio=_round_or_none(upper_wick_ratio),
        lower_wick_ratio=_round_or_none(lower_wick_ratio),
        close_position=_round_or_none(close_position),
        range_atr_ratio=_round_or_none(range_atr_ratio),
        ema_relation=ema_relation,
        overlap_prev_ratio=_round_or_none(overlap_prev_ratio),
        inside_sequence=inside_sequence,
        ioi_pattern=ioi_pattern,
        micro_double=micro_double,
        gap_bar=gap_bar,
        ema_gap_count=ema_gap_count,
        breakout_prev=breakout_prev,
        follow_through_1_2=follow_through_1_2,
        volume_ratio_20=vol_ratio,
        volume_state=vol_state,
    )


def _classify_bar(
    bar: KlineBar,
    prev: KlineBar | None,
    body_ratio: float | None,
    close_position: float | None,
) -> str:
    if prev is not None:
        if bar.high <= prev.high and bar.low >= prev.low:
            return "inside"
        if bar.high >= prev.high and bar.low <= prev.low:
            return "outside_bull" if bar.close >= bar.open else "outside_bear"

    if body_ratio is None or close_position is None:
        return "flat"
    if body_ratio <= Bar.DOJI_BODY_RATIO_MAX:
        return "doji"
    if bar.close > bar.open and close_position >= Bar.TREND_BULL_CLOSE_POS_MIN:
        return "trend_bull"
    if bar.close < bar.open and close_position <= Bar.TREND_BEAR_CLOSE_POS_MAX:
        return "trend_bear"
    return "other"


def _is_inside(bar: KlineBar | None, prev: KlineBar | None) -> bool:
    if bar is None or prev is None:
        return False
    return bar.high <= prev.high and bar.low >= prev.low


def _is_outside(bar: KlineBar | None, prev: KlineBar | None) -> bool:
    if bar is None or prev is None:
        return False
    return bar.high >= prev.high and bar.low <= prev.low


def _inside_sequence(
    bar: KlineBar,
    prev: KlineBar | None,
    prev2: KlineBar | None,
    prev3: KlineBar | None,
) -> str:
    if _is_inside(bar, prev) and _is_inside(prev, prev2) and _is_inside(prev2, prev3):
        return "iii"
    if _is_inside(bar, prev) and _is_inside(prev, prev2):
        return "ii"
    return "none"


def _is_ioi(
    bar: KlineBar,
    prev: KlineBar | None,
    prev2: KlineBar | None,
    prev3: KlineBar | None,
) -> bool:
    return _is_inside(prev2, prev3) and _is_outside(prev, prev2) and _is_inside(bar, prev)


def _micro_double(bar: KlineBar, prev: KlineBar | None, *, atr: float) -> str:
    if prev is None:
        return "none"
    tolerance = 0.0
    if not math.isnan(atr) and atr > 0:
        tolerance = atr * Bar.MICRO_DOUBLE_ATR_TOL
    if abs(bar.low - prev.low) <= tolerance:
        return "MDB"
    if abs(bar.high - prev.high) <= tolerance:
        return "MDT"
    return "none"


def _gap_bar(bar: KlineBar, ema: float) -> str:
    if math.isnan(ema):
        return "none"
    if bar.low > ema:
        return "bull_gap"
    if bar.high < ema:
        return "bear_gap"
    return "none"


def _ema_gap_count(
    bars: tuple[KlineBar, ...],
    idx: int,
    ema_values: tuple[float, ...],
) -> int:
    if idx >= len(ema_values):
        return 0
    ema = ema_values[idx]
    if math.isnan(ema):
        return 0
    side = _gap_bar(bars[idx], ema)
    if side == "none":
        return 0
    count = 0
    for j in range(idx, len(bars)):
        if j >= len(ema_values) or math.isnan(ema_values[j]):
            break
        if _gap_bar(bars[j], ema_values[j]) != side:
            break
        count += 1
    return count


def _breakout_prev_range(
    bars: tuple[KlineBar, ...],
    idx: int,
    *,
    lookback: int = Bar.BREAKOUT_LOOKBACK,
) -> str:
    prev_bars = bars[idx + 1 : idx + 1 + lookback]
    if not prev_bars:
        return "none"
    broke_high = bars[idx].high > max(b.high for b in prev_bars)
    broke_low = bars[idx].low < min(b.low for b in prev_bars)
    if broke_high and broke_low:
        return "both"
    if broke_high:
        return "up"
    if broke_low:
        return "down"
    return "none"


def _follow_through_1_2(bars: tuple[KlineBar, ...], idx: int) -> str:
    if idx == 0:
        return "pending"
    bar = bars[idx]
    newer = bars[max(0, idx - 2) : idx]
    if not newer:
        return "pending"
    direction = 1 if bar.close > bar.open else -1 if bar.close < bar.open else 0
    if direction == 0:
        return "pending"
    same = 0
    opposite = 0
    for nbar in newer:
        if direction > 0:
            same += int(nbar.close > bar.close)
            opposite += int(nbar.close < bar.open)
        else:
            same += int(nbar.close < bar.close)
            opposite += int(nbar.close > bar.open)
    if same > 0:
        return "yes"
    if opposite > 0:
        return "failed"
    return "no"


def _overlap_ratio(bar: KlineBar, prev: KlineBar | None) -> float | None:
    if prev is None:
        return None
    high = min(bar.high, prev.high)
    low = max(bar.low, prev.low)
    overlap = max(0.0, high - low)
    denominator = max(bar.high, prev.high) - min(bar.low, prev.low)
    if denominator <= 0:
        return None
    return overlap / denominator


def _volume_ratio_state(
    bars: tuple[KlineBar, ...], idx: int
) -> tuple[float | None, str]:
    bar = bars[idx]
    if bar.volume is None or bar.volume <= 0:
        return None, "无数据"
    prev = [
        b.volume
        for b in bars[idx + 1 : idx + 1 + Bar.VOLUME_LOOKBACK]
        if b.volume and b.volume > 0
    ]
    if not prev:
        return None, "无数据"
    mean_prev = sum(prev) / len(prev)
    if mean_prev <= 0:
        return None, "无数据"
    ratio = bar.volume / mean_prev
    if ratio >= Bar.VOLUME_HIGH_RATIO:
        state = "放量"
    elif ratio <= Bar.VOLUME_LOW_RATIO:
        state = "缩量"
    else:
        state = "常量"
    return round(ratio, 2), state


def _round_or_none(value: float | None) -> float | None:
    if value is None or math.isnan(value):
        return None
    return round(value, 3)
