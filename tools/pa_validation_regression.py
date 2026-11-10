"""T1.6 pa_validation 等价回归（原研究数据缺失，使用等价样本）。

原 `PA_Agent/research/pa_validation/test3/test5/test6` 依赖
`d:\\MT5_K线数据`（实测不存在），无法复现历史统计。本工具改为：

1. 在 8 类合成行情（可选 v1 记录）上，用 **同一份 bars** 分别跑 v1/v2 特征层；
2. 复算三项研究定义：
   - test3：微通道第一次反向突破失败率（纯几何，v1/v2 各跑一遍）；
   - test5：趋势棒跟随率（按强/弱分桶，比较 v1/v2 单棒特征）；
   - test6：回撤深度分桶（tight/normal/broad/deep，比较 v1/v2 特征）；
3. 输出字段级 parity 与统计，报告中明确标注 `degraded` 来源。

用法：
    python tools/pa_validation_regression.py
    python tools/pa_validation_regression.py --record FILE --md md/09_回放与一致性报告.md
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pa_engine.adapters.v1_bridge import load_feature_layer  # noqa: E402
from pa_engine.adapters.v1_records import load_record  # noqa: E402

MIN_LEN = 4
MAX_LEN = 10
RESUME_HORIZON = 10
FOLLOW_HORIZON = 2
CONT_HORIZON = 30
MIN_LEG_ATR = 2.0


def _chrono(frame) -> list[dict[str, float]]:
    return [
        {
            "o": float(b.open),
            "h": float(b.high),
            "l": float(b.low),
            "c": float(b.close),
        }
        for b in reversed(frame.bars)
    ]


def _atr_chrono(frame) -> list[float]:
    values = list(reversed(frame.indicators.atr14))
    return [float(v) if v is not None else math.nan for v in values]


def _pct(ok: int, total: int) -> float | None:
    return round(ok / total, 4) if total else None


def _detect_micro(bars: list[dict], bull: bool) -> list[tuple[int, int]]:
    n = len(bars)
    out: list[tuple[int, int]] = []
    i = 1
    while i < n - 1:
        j = i
        while j + 1 < n:
            cur, nxt = bars[j], bars[j + 1]
            if bull:
                ok = nxt["h"] > cur["h"] and nxt["l"] >= cur["l"] - 1e-9
            else:
                ok = nxt["l"] < cur["l"] and nxt["h"] <= cur["h"] + 1e-9
            if not ok:
                break
            j += 1
        length = j - i + 1
        span = range(i, j + 1)
        if bull:
            mostly = sum(1 for k in span if bars[k]["c"] > bars[k]["o"]) >= 0.75 * length
        else:
            mostly = sum(1 for k in span if bars[k]["c"] < bars[k]["o"]) >= 0.75 * length
        if MIN_LEN <= length <= MAX_LEN and mostly:
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def test3_micro_break_failure(bars: list[dict]) -> dict[str, Any]:
    breaks = failed = channels = 0
    for bull in (True, False):
        found = _detect_micro(bars, bull)
        channels += len(found)
        for _, end in found:
            brk = None
            for k in range(end + 1, min(end + 1 + RESUME_HORIZON, len(bars))):
                if bull and bars[k]["l"] < bars[k - 1]["l"]:
                    brk = k
                    break
                if not bull and bars[k]["h"] > bars[k - 1]["h"]:
                    brk = k
                    break
            if brk is None:
                continue
            breaks += 1
            resumed = extended = False
            for k in range(brk + 1, min(brk + 1 + RESUME_HORIZON, len(bars))):
                if bull:
                    if bars[k]["h"] > bars[brk]["h"]:
                        resumed = True
                        break
                    if bars[k]["l"] < bars[brk]["l"]:
                        extended = True
                        break
                else:
                    if bars[k]["l"] < bars[brk]["l"]:
                        resumed = True
                        break
                    if bars[k]["h"] > bars[brk]["h"]:
                        extended = True
                        break
            failed += int(resumed and not extended)
    return {
        "channels": channels,
        "first_breaks": breaks,
        "failed": failed,
        "failure_rate": _pct(failed, breaks),
    }


def test5_follow_through(
    features: list[Any], bars: list[dict], atr: list[float]
) -> dict[str, Any]:
    buckets = {"strong": [0, 0], "weak": [0, 0]}
    # features 是 newest-first；用时间序列更直观
    chrono = list(reversed(features))
    for i in range(0, len(chrono) - FOLLOW_HORIZON):
        feature = chrono[i]
        if feature.bar_type not in ("trend_bull", "trend_bear"):
            continue
        bar = bars[i]
        full = bar["h"] - bar["l"]
        body = abs(bar["c"] - bar["o"])
        strong = bool(atr[i] and atr[i] > 0 and body >= atr[i] and full > 0 and body / full >= 0.6)
        d = 1 if feature.bar_type == "trend_bull" else -1
        follow = False
        for k in range(i + 1, i + 1 + FOLLOW_HORIZON):
            if d > 0 and bars[k]["c"] > bar["c"]:
                follow = True
                break
            if d < 0 and bars[k]["c"] < bar["c"]:
                follow = True
                break
        key = "strong" if strong else "weak"
        buckets[key][0] += int(follow)
        buckets[key][1] += 1
    return {
        key: {"follow": value[0], "total": value[1], "rate": _pct(value[0], value[1])}
        for key, value in buckets.items()
    }


def _pivots(bars: list[dict]) -> list[tuple[int, str, float]]:
    pivots = []
    for i in range(1, len(bars) - 1):
        if bars[i]["h"] > bars[i - 1]["h"] and bars[i]["h"] > bars[i + 1]["h"]:
            pivots.append((i, "H", bars[i]["h"]))
        if bars[i]["l"] < bars[i - 1]["l"] and bars[i]["l"] < bars[i + 1]["l"]:
            pivots.append((i, "L", bars[i]["l"]))
    return pivots


def test6_pullback_buckets(bars: list[dict], atr: list[float]) -> dict[str, Any]:
    piv = _pivots(bars)
    buckets = {"tight": [0, 0], "normal": [0, 0], "broad": [0, 0], "deep": [0, 0]}
    for idx in range(len(piv) - 2):
        a, c, pb = piv[idx], piv[idx + 1], piv[idx + 2]
        for bull in (True, False):
            if bull and not (a[1] == "L" and c[1] == "H" and pb[1] == "L"):
                continue
            if not bull and not (a[1] == "H" and c[1] == "L" and pb[1] == "H"):
                continue
            lo_p, hi_p = (a[2], c[2]) if bull else (c[2], a[2])
            atr_ref = atr[c[0]]
            if not atr_ref or math.isnan(atr_ref) or atr_ref <= 0:
                continue
            leg = hi_p - lo_p
            if leg < MIN_LEG_ATR * atr_ref:
                continue
            depth = (hi_p - pb[2]) / leg if bull else (pb[2] - lo_p) / leg
            if depth <= 0 or depth > 1.2:
                continue
            cont = False
            for k in range(pb[0] + 1, min(pb[0] + 1 + CONT_HORIZON, len(bars))):
                if bull:
                    if bars[k]["h"] > hi_p:
                        cont = True
                        break
                    if bars[k]["c"] < lo_p:
                        break
                else:
                    if bars[k]["l"] < lo_p:
                        cont = True
                        break
                    if bars[k]["c"] > hi_p:
                        break
            key = (
                "tight"
                if depth < 0.30
                else "normal"
                if depth < 0.50
                else "broad"
                if depth < 0.786
                else "deep"
            )
            buckets[key][0] += int(cont)
            buckets[key][1] += 1
    return {
        key: {"continued": value[0], "total": value[1], "rate": _pct(value[0], value[1])}
        for key, value in buckets.items()
    }


def _compare_features(v1_features: list[Any], v2_features: list[Any]) -> dict[str, Any]:
    mismatches = 0
    samples = min(len(v1_features), len(v2_features))
    for i in range(samples):
        a, b = v1_features[i], v2_features[i]
        if a.bar_type != b.bar_type or a.follow_through_1_2 != b.follow_through_1_2:
            mismatches += 1
    return {"samples": samples, "mismatches": mismatches, "rate": _pct(samples - mismatches, samples)}


def _run_frame(frame_v1, frame_v2) -> dict[str, Any]:
    v1_layer = load_feature_layer()
    v2_layer = __import__("pa_engine.features.kline", fromlist=["compute_kline_geometry_features"])
    v1_features = v1_layer["compute_kline_geometry_features"](frame_v1)
    v2_features = v2_layer.compute_kline_geometry_features(frame_v2)
    features_parity = _compare_features(v1_features, v2_features)

    bars_v2 = _chrono(frame_v2)
    bars_v1 = _chrono(frame_v1)
    atr = _atr_chrono(frame_v2)
    micro_v2 = test3_micro_break_failure(bars_v2)
    # v1/v2 使用同一 bars：微通道检测结果必然一致；这里显式复算两次以验证数据层
    micro_v1 = test3_micro_break_failure(bars_v1)
    follow_v1 = test5_follow_through(v1_features, bars_v1, atr)
    follow_v2 = test5_follow_through(v2_features, bars_v2, atr)

    depth_v1 = v1_layer["compute_simple_market_features"](frame_v1).pullback_depth_atr
    depth_v2 = __import__(
        "pa_engine.features.structure", fromlist=["compute_simple_market_features"]
    ).compute_simple_market_features(frame_v2).pullback_depth_atr

    return {
        "micro": {"v1": micro_v1, "v2": micro_v2},
        "follow": {"v1": follow_v1, "v2": follow_v2},
        "features_parity": features_parity,
        "pullback_depth": {"v1": depth_v1, "v2": depth_v2, "bucket": test6_pullback_buckets(bars_v2, atr)},
        "pullback_parity": depth_v1 == depth_v2,
    }


def _build_frames(rows: list[dict]):
    # 复用 compare 工具的构造器，保证 v1/v2 指标同源
    from tools.compare_v1_v2 import _build_frames as build

    return build(rows)


def _synthetic_results() -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    from tests.fixtures.synthetic import REGIMES, make_regime_rows

    out = {}
    for regime in REGIMES:
        out[regime] = _run_frame(*_build_frames(make_regime_rows(regime)))
    return out


def _record_results(path: str) -> dict[str, Any]:
    from tools.compare_v1_v2 import _rows_from_record

    record = load_record(path)
    return {"record": _run_frame(*_build_frames(_rows_from_record(record)))}


def _render_md(results: dict[str, Any]) -> str:
    lines = [
        "## 4. pa_validation 等价回归（T1.6）",
        "",
        "> 原研究数据 `d:\\MT5_K线数据` 不存在，以下为合成样本上的等价回归（degraded）。",
        "",
        "| 样本 | 微通道数 | 首次反向突破 | 失败率 | 强趋势棒跟随 | 弱趋势棒跟随 | 单棒特征 parity |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, item in results.items():
        micro = item["micro"]["v2"]
        strong = item["follow"]["v2"]["strong"]
        weak = item["follow"]["v2"]["weak"]
        lines.append(
            f"| {name} | {micro['channels']} | {micro['first_breaks']} | "
            f"{micro['failure_rate'] if micro['failure_rate'] is not None else 'N/A'} | "
            f"{strong['rate'] if strong['rate'] is not None else 'N/A'} | "
            f"{weak['rate'] if weak['rate'] is not None else 'N/A'} | "
            f"{item['features_parity']['rate']:.2%} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", default=None)
    parser.add_argument("--md", default=None)
    parser.add_argument("--json-out", default=str(ROOT / "records" / "pa_validation" / "report.json"))
    args = parser.parse_args(argv)

    try:
        load_feature_layer()
    except Exception as exc:
        print(f"v1 不可用：{type(exc).__name__}: {exc}")
        return 1

    results = _record_results(args.record) if args.record else _synthetic_results()
    payload = {
        "status": "degraded_missing_research_data",
        "note": "原 research/pa_validation 依赖的 d:\\MT5_K线数据 不存在；使用合成样本等价回归。",
        "samples": len(results),
        "results": results,
    }
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    for name, item in results.items():
        parity = item["features_parity"]
        print(
            f"{name}: micro_breaks={item['micro']['v2']['first_breaks']} "
            f"follow_parity={item['follow']['v2']['weak']['rate']} "
            f"features_parity={parity['rate']:.2%} ({parity['mismatches']} mismatches)"
        )
    if args.md:
        from pa_engine.records.audit import upsert_section

        upsert_section(
            Path(args.md),
            "pa_validation",
            _render_md(results),
            title="# 09 回放与一致性报告",
        )
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
