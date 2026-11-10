"""同一快照 v1/v2 对照：预填、趋势背景、结构特征字段级差异（T2.7）。

v1 只读；无 v1 时明确报错退出。
用法：
    python tools/compare_v1_v2.py                 # 8 类合成行情
    python tools/compare_v1_v2.py --record FILE   # 单条 v1 记录
    python tools/compare_v1_v2.py --md md/09_回放与一致性报告.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pa_engine.adapters.v1_bridge import load_feature_layer, load_prefill_layer  # noqa: E402
from pa_engine.adapters.v1_records import load_record  # noqa: E402
from pa_engine.data.types import KlineBar, KlineFrame, build_frame  # noqa: E402
from pa_engine.features.structure import compute_simple_market_features  # noqa: E402
from pa_engine.prefill import build_trend_context as v2_trend_context  # noqa: E402
from pa_engine.prefill import judge_always_in as v2_judge_always_in  # noqa: E402
from pa_engine.prefill import judge_direction as v2_judge_direction  # noqa: E402

FEATURE_FIELDS = (
    "lookback_bars",
    "range_high",
    "range_low",
    "range_width_atr",
    "price_position",
    "zone",
    "dist_to_high_atr",
    "dist_to_low_atr",
    "overlap_mean_10",
    "doji_inside_ratio_10",
    "barbwire_score",
    "barbwire_candidate",
    "swing_structure",
    "pullback_depth_atr",
    "pullback_bars",
    "breakout_quality",
    "breakout_attempt_type",
    "mm_as_tp_ok",
    "spike_aftermath_hint",
    "scale_conflict",
)
_NUMERIC_TOL = 1e-6


def _rows_from_record(record: dict[str, Any]) -> list[dict]:
    raw = record.get("kline_data") or []
    rows = [dict(item) for item in raw]
    if len(rows) >= 2 and float(rows[0]["ts_open"]) < float(rows[-1]["ts_open"]):
        rows = list(reversed(rows))
    return list(reversed(rows))  # oldest-first


def _build_frames(rows: list[dict]) -> tuple[Any, KlineFrame]:
    """rows oldest-first → (v1 frame, v2 frame)。"""
    from pa_agent.data.base import IndicatorBundle as V1Indicators
    from pa_agent.data.base import KlineBar as V1Bar
    from pa_agent.data.base import KlineFrame as V1Frame
    from pa_agent.indicators.atr import atr_full as v1_atr
    from pa_agent.indicators.ema import ema_full as v1_ema

    newest = list(reversed(rows))
    v1_bars = [
        V1Bar(
            seq=index,
            ts_open=float(row["ts_open"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume") or 0.0),
        )
        for index, row in enumerate(newest, start=1)
    ]
    v2_bars = [
        KlineBar(
            seq=bar.seq,
            ts_open=bar.ts_open,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
        )
        for bar in v1_bars
    ]
    chrono = list(reversed(v1_bars))
    closes = [b.close for b in chrono]
    highs = [b.high for b in chrono]
    lows = [b.low for b in chrono]
    ema = tuple(reversed(v1_ema(closes, 20)))
    atr = tuple(reversed(v1_atr(highs, lows, closes, 14)))
    frame_v1 = V1Frame(
        symbol="COMPARE",
        timeframe="1h",
        bars=tuple(v1_bars),
        indicators=V1Indicators(ema20=ema, atr14=atr),
        snapshot_ts_local_ms=0,
    )
    frame_v2 = build_frame("COMPARE", "1h", v2_bars)
    return frame_v1, frame_v2


def _num_equal(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a is b
        fa, fb = float(a), float(b)
        if fa != fa and fb != fb:  # 两侧都是 NaN 视为一致
            return True
        return abs(fa - fb) <= _NUMERIC_TOL
    return a == b


def compare_snapshot(rows: list[dict]) -> dict[str, Any]:
    frame_v1, frame_v2 = _build_frames(rows)
    prefill = load_prefill_layer()
    direction_v1, _ = prefill["judge_direction"](frame_v1)
    ai_v1 = prefill["judge_always_in"](frame_v1)
    ctx_v1 = prefill["build_trend_context"](frame_v1, direction_v1)

    direction_v2 = v2_judge_direction(frame_v2).direction
    ai_v2 = v2_judge_always_in(frame_v2)
    ctx_v2 = v2_trend_context(frame_v2, direction_v2)

    features_v1 = load_feature_layer()["compute_simple_market_features"](frame_v1)
    features_v2 = compute_simple_market_features(frame_v2)

    fields: dict[str, dict] = {}

    def put(name: str, a: Any, b: Any) -> None:
        fields[name] = {"v1": a, "v2": b, "ok": _num_equal(a, b)}

    put("direction", direction_v1, direction_v2)
    ai_map = {"AIL": "bullish", "AIS": "bearish", "neutral": "none", None: "none"}
    put("always_in", ai_map.get(ai_v1.branch, ai_v1.branch), ai_v2.always_in)
    for key in ("background_direction", "relationship", "conflict"):
        put(f"trend.{key}", ctx_v1.get(key), ctx_v2.get(key))
    for name in FEATURE_FIELDS:
        put(name, getattr(features_v1, name, None), getattr(features_v2, name, None))
    put("hl.bull_count", features_v1.hl_count.bull_count, features_v2.hl_count.bull_count)
    put("hl.bear_count", features_v1.hl_count.bear_count, features_v2.hl_count.bear_count)
    put("supports", list(features_v1.supports), list(features_v2.supports))
    put("resistances", list(features_v1.resistances), list(features_v2.resistances))

    diffs = {name: value for name, value in fields.items() if not value["ok"]}
    return {"fields": fields, "diffs": diffs, "matched": len(diffs) == 0}


def _synthetic_rows() -> dict[str, list[dict]]:
    # v1 根目录可能也有 tests/，必须把本仓库根目录重新置顶，避免同名包遮蔽
    sys.path.insert(0, str(ROOT))
    from tests.fixtures.synthetic import REGIMES, make_regime_rows

    return {regime: make_regime_rows(regime) for regime in REGIMES}


def _render_md(result: dict[str, Any], title: str, *, level: str = "##") -> str:
    lines = [
        f"{level} {title}",
        "",
        f"- 字段数：{len(result['fields'])}，差异：{len(result['diffs'])}",
        "",
        "| 字段 | v1 | v2 | 一致 |",
        "| --- | --- | --- | :---: |",
    ]
    for name, item in result["fields"].items():
        lines.append(
            f"| {name} | {item['v1']!r} | {item['v2']!r} | {'是' if item['ok'] else '否'} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", default=None, help="单条 v1 记录 JSON")
    parser.add_argument("--md", default=None)
    parser.add_argument("--json-out", default=str(ROOT / "records" / "compare" / "report.json"))
    args = parser.parse_args(argv)

    try:
        load_prefill_layer()
    except Exception as exc:
        print(f"v1 不可用：{type(exc).__name__}: {exc}")
        return 1

    results: dict[str, Any] = {}
    if args.record:
        record = load_record(args.record)
        results[Path(args.record).stem] = compare_snapshot(_rows_from_record(record))
    else:
        for name, rows in _synthetic_rows().items():
            results[name] = compare_snapshot(rows)

    payload = {
        "snapshots": len(results),
        "matched": sum(1 for value in results.values() if value["matched"]),
        "results": results,
    }
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    for name, result in results.items():
        print(f"{name}: fields={len(result['fields'])} diffs={len(result['diffs'])}")
        for field, value in result["diffs"].items():
            print(f"  {field}: v1={value['v1']!r} v2={value['v2']!r}")

    if args.md:
        from pa_engine.records.audit import upsert_section

        blocks = ["## 3. v1/v2 字段级对照", ""]
        blocks += [_render_md(result, name, level="###") for name, result in results.items()]
        upsert_section(
            Path(args.md),
            "compare",
            "\n".join(blocks),
            title="# 09 回放与一致性报告",
        )
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
