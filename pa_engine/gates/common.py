"""闸门共享的测量函数：尖峰run、回撤百分比、趋势棒等。"""
from __future__ import annotations

from dataclasses import dataclass

from pa_engine.config.thresholds import Prefill
from pa_engine.data.types import KlineBar
from pa_engine.features.structure import SwingPivot, find_confirmed_swings, trend_bar_counts


@dataclass(frozen=True)
class SpikeRun:
    direction: str  # bullish / bearish
    length: int
    start_seq: int
    end_seq: int
    body_overlap: float
    origin: float
    end_close: float

    @property
    def height(self) -> float:
        return abs(self.end_close - self.origin)


def detect_last_spike_run(
    bars: tuple[KlineBar, ...],
    atr: float | None,
    *,
    lookback: int = 16,
) -> SpikeRun | None:
    """识别最近一段连续同向趋势棒（复刻 v1 `_spike_aftermath_hint` 的 run 扫描）。"""
    if not bars or not atr or atr <= 0:
        return None
    chrono = list(reversed(bars[:lookback]))
    runs: list[tuple[int, int, int]] = []
    i = 0
    while i < len(chrono):
        o, c = float(chrono[i].open), float(chrono[i].close)
        d = 1 if c > o else (-1 if c < o else 0)
        if d == 0 or abs(c - o) < 0.5 * atr:
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
        if len(run) >= 2:
            runs.append((run[0], run[-1], d))
        i = j if j > i + 1 else i + 1

    if not runs:
        return None
    start_i, end_i, d = runs[-1]
    run_bars = chrono[start_i : end_i + 1]
    direction = "bullish" if d > 0 else "bearish"
    origin = float(run_bars[0].low) if d > 0 else float(run_bars[0].high)
    end_close = float(run_bars[-1].close)
    return SpikeRun(
        direction=direction,
        length=len(run_bars),
        start_seq=int(run_bars[0].seq),
        end_seq=int(run_bars[-1].seq),
        body_overlap=_body_overlap(run_bars),
        origin=origin,
        end_close=end_close,
    )


def _body_overlap(run_bars: list[KlineBar]) -> float:
    vals: list[float] = []
    for prev, cur in zip(run_bars, run_bars[1:]):
        p_lo, p_hi = sorted((float(prev.open), float(prev.close)))
        c_lo, c_hi = sorted((float(cur.open), float(cur.close)))
        overlap = max(0.0, min(p_hi, c_hi) - max(p_lo, c_lo))
        union = max(p_hi, c_hi) - min(p_lo, c_lo)
        if union > 0:
            vals.append(overlap / union)
    return round(sum(vals) / len(vals), 3) if vals else 0.0


def pullback_after_run(
    bars: tuple[KlineBar, ...], run: SpikeRun
) -> tuple[float | None, float]:
    """返回 (回撤占尖峰高度百分比, 回撤极值)。无后续棒时返回 (0.0, end_close)。"""
    after = [b for b in bars if int(b.seq) < run.end_seq]
    if not after:
        return 0.0, run.end_close
    if run.direction == "bullish":
        extreme = min(float(b.low) for b in after)
        depth = max(0.0, run.end_close - extreme)
    else:
        extreme = max(float(b.high) for b in after)
        depth = max(0.0, extreme - run.end_close)
    height = run.height if run.height > 0 else 1.0
    return round(depth / height, 4), extreme


def leg_pullback_pct(
    bars: tuple[KlineBar, ...], direction: str, *, window: int = 40
) -> float | None:
    """当前回撤占最近一腿高度的百分比（用于 §5 通道宽度主判据）。

    口径：取「当前收盘之外最近的已确认摆动极值」与其前一个反向枢轴构成最后一腿，
    回撤深度 = 极值到当前收盘；若当前已创新高/新低（无更高高点）则回撤为 0。
    """
    if not bars:
        return None
    close = float(bars[0].close)
    pivots = find_confirmed_swings(bars, window)
    highs = sorted([p for p in pivots if p.kind == "high"], key=lambda p: p.seq)
    lows = sorted([p for p in pivots if p.kind == "low"], key=lambda p: p.seq)
    if direction == "bullish":
        for high in highs:
            if high.price <= close:
                continue
            older_lows = [p for p in lows if p.seq > high.seq]
            if not older_lows:
                continue
            low = min(older_lows, key=lambda p: p.seq)
            leg = high.price - low.price
            if leg <= 0:
                continue
            depth = max(0.0, high.price - close)
            return round(depth / leg, 4)
        return 0.0
    if direction == "bearish":
        for low in lows:
            if low.price >= close:
                continue
            older_highs = [p for p in highs if p.seq > low.seq]
            if not older_highs:
                continue
            high = min(older_highs, key=lambda p: p.seq)
            leg = high.price - low.price
            if leg <= 0:
                continue
            depth = max(0.0, close - low.price)
            return round(depth / leg, 4)
        return 0.0
    return None


def trend_ratio(bars: tuple[KlineBar, ...], window: int) -> tuple[int, int, float]:
    bull, bear = trend_bar_counts(bars, window)
    total = max(window, 1)
    return bull, bear, (bull + bear) / total


def latest_direction(bars: tuple[KlineBar, ...], window: int = 8) -> str:
    """近端方向：收盘重心（加权）简单比较，用于闸门内部证据。"""
    sample = list(bars)[:window]
    if len(sample) < 2:
        return "neutral"
    half = len(sample) // 2
    near = sum(float(b.close) for b in sample[:half]) / half
    far = sum(float(b.close) for b in sample[half : 2 * half]) / max(half, 1)
    if near > far:
        return "bullish"
    if near < far:
        return "bearish"
    return "neutral"


def swing_groups(bars: tuple[KlineBar, ...], window: int = 40) -> tuple[int, str]:
    """返回 (HH+HL/LL+LH 有效组数, 方向)。"""
    from pa_engine.features.structure import count_hh_hl_groups

    groups = count_hh_hl_groups(bars, window)
    pivots = find_confirmed_swings(bars, window)
    highs = [p for p in pivots if p.kind == "high"]
    lows = [p for p in pivots if p.kind == "low"]
    direction = "neutral"
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[0].price > highs[1].price and lows[0].price > lows[1].price:
            direction = "bullish"
        elif highs[0].price < highs[1].price and lows[0].price < lows[1].price:
            direction = "bearish"
    return groups, direction
