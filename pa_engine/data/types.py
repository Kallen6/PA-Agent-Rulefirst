"""最小数据镜像：与 v1 `pa_agent.data.base` 字段完全一致，便于 parity 与替换。

约定：
- `KlineFrame.bars` 最新在前（bars[0].seq == 1）；
- 指标序列与 bars 等长、同序；
- 分析帧只含已收盘 K 线（forming bar 在 get_display_frame 之前剔除）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class KlineBar:
    seq: int
    ts_open: float  # Unix ms (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float | None = 0.0
    amount: float = 0.0
    pct_chg: float | None = None
    closed: bool = True


@dataclass(frozen=True)
class IndicatorBundle:
    ema20: tuple[float, ...] = ()
    atr14: tuple[float, ...] = ()


@dataclass(frozen=True)
class KlineFrame:
    symbol: str
    timeframe: str
    bars: tuple[KlineBar, ...]
    indicators: IndicatorBundle = field(default_factory=IndicatorBundle)
    snapshot_ts_local_ms: int = 0

    @property
    def newest(self) -> KlineBar | None:
        return self.bars[0] if self.bars else None


def build_frame(
    symbol: str,
    timeframe: str,
    bars_newest_first: list[KlineBar],
    *,
    snapshot_ts_local_ms: int = 0,
) -> KlineFrame:
    """从「最新在前」的 bars 构建帧，并按最老→最新顺序计算 EMA20/ATR14。"""
    from pa_engine.features.indicators import atr_full, ema_full

    bars = tuple(bars_newest_first)
    chronological = list(reversed(bars))
    closes = [float(b.close) for b in chronological]
    highs = [float(b.high) for b in chronological]
    lows = [float(b.low) for b in chronological]

    ema = ema_full(closes, 20)
    atr = atr_full(highs, lows, closes, 14)
    indicators = IndicatorBundle(
        ema20=tuple(reversed(ema)),
        atr14=tuple(reversed(atr)),
    )
    return KlineFrame(
        symbol=symbol,
        timeframe=timeframe,
        bars=bars,
        indicators=indicators,
        snapshot_ts_local_ms=snapshot_ts_local_ms,
    )


def is_finite(value: float | None) -> bool:
    return value is not None and not math.isnan(value)
