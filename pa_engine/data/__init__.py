"""数据层：v2 类型镜像 + 币安行情抓取。

- `types`: KlineBar / KlineFrame / IndicatorBundle（与 v1 字段一致）
- `binance`: 币安 K 线抓取（标准库 urllib，`run.py --live` 的入口），
  现货 `/api/v3/klines`、合约 `/fapi/v1/klines`，公共行情免鉴权
- v1 的 7 种数据源仍可通过 `pa_engine.adapters.v1_bridge` 只读复用（仅测试/回放）
"""
from pa_engine.data.types import (
    IndicatorBundle,
    KlineBar,
    KlineFrame,
    build_frame,
    is_finite,
)

__all__ = [
    "IndicatorBundle",
    "KlineBar",
    "KlineFrame",
    "build_frame",
    "is_finite",
]
