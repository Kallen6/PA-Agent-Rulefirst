"""EMA / ATR 指标（从 v1 `pa_agent/indicators` 移植，保证 parity）。

输入一律 oldest-first；输出等长列表，预热期为 nan。
"""
from __future__ import annotations

import math


def ema_full(values: list[float], period: int) -> list[float]:
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    n = len(values)
    result = [math.nan] * n
    if n < period:
        return result

    alpha = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    result[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = values[i] * alpha + prev * (1.0 - alpha)
        result[i] = prev
    return result


def _true_range(high: float, low: float, prev_close: float) -> float:
    hl = abs(high - low)
    if math.isnan(prev_close):
        return hl
    return max(hl, abs(high - prev_close), abs(low - prev_close))


def atr_full(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> list[float]:
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    n = len(highs)
    if n != len(lows) or n != len(closes):
        raise ValueError("highs, lows, closes must have the same length")
    result = [math.nan] * n
    if n < period:
        return result

    trs: list[float] = []
    for i in range(n):
        prev_c = closes[i - 1] if i > 0 else math.nan
        trs.append(_true_range(highs[i], lows[i], prev_c))

    seed = sum(trs[:period]) / period
    result[period - 1] = seed
    prev_atr = seed
    for i in range(period, n):
        prev_atr = (prev_atr * (period - 1) + trs[i]) / period
        result[i] = prev_atr
    return result
