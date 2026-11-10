"""特征层：指标、单根几何、结构特征与紧凑渲染。

- `indicators`: EMA/ATR（移植自 v1）
- `kline`: 单根 18 列几何特征
- `structure`: 摆动/回撤/HL 计数/突破/Barbwire/MM/尖峰余波
- `render`: 灰区 prompt 的紧凑证据块
"""
from pa_engine.features.kline import (
    KlineGeometryFeature,
    bar_candle_direction_label,
    compute_kline_geometry_features,
)
from pa_engine.features.structure import (
    BreakoutEvent,
    HLCountState,
    MeasuredMoveCandidate,
    SimpleMarketFeatures,
    SwingPivot,
    compute_simple_market_features,
    count_hh_hl_groups,
    find_confirmed_swings,
    mean_overlap_ratio,
    trend_bar_counts,
)

__all__ = [
    "KlineGeometryFeature",
    "bar_candle_direction_label",
    "compute_kline_geometry_features",
    "BreakoutEvent",
    "HLCountState",
    "MeasuredMoveCandidate",
    "SimpleMarketFeatures",
    "SwingPivot",
    "compute_simple_market_features",
    "count_hh_hl_groups",
    "find_confirmed_swings",
    "mean_overlap_ratio",
    "trend_bar_counts",
]
