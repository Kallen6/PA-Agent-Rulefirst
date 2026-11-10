"""紧凑证据渲染：把 K 线与结构特征压成 ≤2KB 的文本块（供灰区 prompt）。

与 v1 的完整表格相比：
- 只渲染近端 K10–K1（远端由结构摘要代表）；
- 单表合并 K 线与几何特征，减少表头与对齐字符；
- 结构摘要只列决策相关字段，不重复阈值说明。
"""
from __future__ import annotations

from datetime import datetime, timezone

from pa_engine.data.types import KlineFrame
from pa_engine.features.kline import KlineGeometryFeature, compute_kline_geometry_features
from pa_engine.features.structure import SimpleMarketFeatures, compute_simple_market_features


def _fmt_time(ts_ms: float) -> str:
    try:
        dt = datetime.fromtimestamp(float(ts_ms) / 1000, tz=timezone.utc)
        return dt.strftime("%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "?"


def _fmt(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def _special(feat: KlineGeometryFeature) -> str:
    parts: list[str] = []
    if feat.inside_sequence != "none":
        parts.append(feat.inside_sequence)
    if feat.ioi_pattern:
        parts.append("ioi")
    if feat.micro_double != "none":
        parts.append(feat.micro_double)
    if feat.gap_bar != "none":
        parts.append(f"{feat.gap_bar}:{feat.ema_gap_count}")
    if feat.breakout_prev != "none":
        parts.append(f"brk_{feat.breakout_prev}")
    if feat.follow_through_1_2 != "pending":
        parts.append(f"ft_{feat.follow_through_1_2}")
    return ",".join(parts) if parts else "-"


def render_kline_feature_table(
    frame: KlineFrame,
    features: list[KlineGeometryFeature] | None = None,
    *,
    limit: int = 10,
) -> str:
    feats = features if features is not None else compute_kline_geometry_features(frame)
    rows = [
        "seq|time(UTC)|O|H|L|C|EMA20|ATR14|type|body|closePos|ovlp|special|vol",
    ]
    for bar, feat in zip(frame.bars[:limit], feats[:limit]):
        ema = frame.indicators.ema20[bar.seq - 1] if bar.seq - 1 < len(frame.indicators.ema20) else None
        atr = frame.indicators.atr14[bar.seq - 1] if bar.seq - 1 < len(frame.indicators.atr14) else None
        rows.append(
            "|".join(
                [
                    f"K{bar.seq}",
                    _fmt_time(bar.ts_open),
                    _fmt(bar.open),
                    _fmt(bar.high),
                    _fmt(bar.low),
                    _fmt(bar.close),
                    _fmt(ema),
                    _fmt(atr, 3),
                    feat.bar_type,
                    _fmt(feat.body_ratio),
                    _fmt(feat.close_position),
                    _fmt(feat.overlap_prev_ratio),
                    _special(feat),
                    feat.volume_state,
                ]
            )
        )
    return "\n".join(rows)


def render_structure_evidence(sf: SimpleMarketFeatures) -> str:
    hc = sf.hl_count
    lines = [
        "## 程序结构证据",
        f"- 区间: 高{_fmt(sf.range_high)} 低{_fmt(sf.range_low)} 宽{_fmt(sf.range_width_atr)}ATR "
        f"位置{_fmt(sf.price_position)} {sf.zone}",
        f"- 重叠均值{_fmt(sf.overlap_mean_10)} 十字/内包占比{_fmt(sf.doji_inside_ratio_10)} "
        f"铁丝网评分{sf.barbwire_score:.2f}({ '候选' if sf.barbwire_candidate else '否'})",
        f"- 摆动结构{sf.swing_structure} 回撤{_fmt(sf.pullback_depth_atr)}ATR/{sf.pullback_bars}棒",
        f"- 突破质量{sf.breakout_quality} 尝试{sf.breakout_attempt_type or '-'} MM作TP{sf.mm_as_tp_ok}",
        f"- 尖峰余波{sf.spike_aftermath_hint} 多尺度冲突{sf.scale_conflict}",
        f"- H触发{hc.bull_candidate}({hc.bull_count}) L触发{hc.bear_candidate}({hc.bear_count}) "
        f"范围{hc.bar_range}",
    ]
    if sf.supports or sf.resistances:
        lines.append(
            f"- 支撑{list(sf.supports)} 阻力{list(sf.resistances)}"
        )
    if sf.measured_moves:
        mm = sf.measured_moves[0]
        lines.append(f"- MM候选{mm.kind} 高度{mm.height} 目标{mm.target_price}")
    return "\n".join(lines)


def render_evidence_block(
    frame: KlineFrame,
    *,
    sf: SimpleMarketFeatures | None = None,
    kf: list[KlineGeometryFeature] | None = None,
    limit: int = 10,
) -> str:
    features = sf if sf is not None else compute_simple_market_features(frame)
    kline_features = kf if kf is not None else compute_kline_geometry_features(frame)
    return "\n\n".join(
        [
            "# 近端K线（K10–K1）",
            render_kline_feature_table(frame, kline_features, limit=limit),
            render_structure_evidence(features),
        ]
    )
