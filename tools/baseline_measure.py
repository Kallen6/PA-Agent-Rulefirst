"""P-1 基线测量：真实历史行情滑窗跑纯代码决策（零 LLM 调用）。

统计：
- 周期分布 / gate_result / 终态 / 入场路径；
- 灰区占比（would-call）、灰区原因 TopN；
- 主判测量距阈值的最小余量分布；
- 单帧延迟 p50/p95、灰区 prompt 平均/最大字节、估算 token；
- 多品种 × 多周期可复现汇总。

用法：
    python tools/baseline_measure.py
    python tools/baseline_measure.py --symbols BTCUSDT,ETHUSDT --timeframes 1h,4h --samples 30
    python tools/baseline_measure.py --offline
    python tools/baseline_measure.py --md md/07_基线测量.md

网络失败时优先读缓存；无缓存则该序列降级跳过，报告标注 degraded。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pa_engine.ai.prompt import build_gray_zone_prompt, prompt_size  # noqa: E402
from pa_engine.config.thresholds import GrayZone as GZ  # noqa: E402
from pa_engine.data.binance import KlineFetchError, fetch_klines_history  # noqa: E402
from pa_engine.data.types import KlineBar, build_frame  # noqa: E402
from pa_engine.engine.gray_zone import (  # noqa: E402
    contested_measurements,
    measurements_from_results,
)
from pa_engine.engine.pipeline import decide  # noqa: E402

DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT"
DEFAULT_TIMEFRAMES = "15m,1h,4h"
CACHE_DIR = ROOT / "records" / "baseline" / "klines"
DEFAULT_JSON = ROOT / "records" / "baseline" / "summary.json"
DEFAULT_MD = ROOT / "md" / "07_基线测量.md"


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[index]


def _cache_path(symbol: str, timeframe: str) -> Path:
    return CACHE_DIR / f"{symbol}_{timeframe}.json"


def _load_cache(symbol: str, timeframe: str) -> list[KlineBar] | None:
    path = _cache_path(symbol, timeframe)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return [KlineBar(**item) for item in raw]
    except (OSError, ValueError, TypeError):
        return None


def _save_cache(symbol: str, timeframe: str, bars: list[KlineBar]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "seq": bar.seq,
            "ts_open": bar.ts_open,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for bar in bars
    ]
    _cache_path(symbol, timeframe).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _get_bars(
    symbol: str,
    timeframe: str,
    count: int,
    *,
    offline: bool,
    refresh: bool,
) -> tuple[list[KlineBar] | None, str]:
    if not refresh:
        cached = _load_cache(symbol, timeframe)
        if cached and len(cached) >= count:
            return cached[:count], "cache"
    if offline:
        return None, "offline-no-cache"
    try:
        bars = fetch_klines_history(
            symbol=symbol, timeframe=timeframe, count=count, request_interval_seconds=0.15
        )
    except KlineFetchError as exc:
        cached = _load_cache(symbol, timeframe)
        if cached:
            return cached[:count], f"cache-fallback（{type(exc).__name__}）"
        return None, f"error: {exc}"
    _save_cache(symbol, timeframe, bars)
    return bars, "network"


def _window_frame(symbol: str, timeframe: str, bars: list[KlineBar], offset: int, window: int):
    from dataclasses import replace

    chunk = bars[offset : offset + window]
    reseq = [replace(bar, seq=index) for index, bar in enumerate(chunk, start=1)]
    return build_frame(symbol, timeframe, reseq)


def _measure_series(
    symbol: str,
    timeframe: str,
    bars: list[KlineBar],
    *,
    samples: int,
    window: int,
    step: int,
    band: float,
) -> dict[str, Any]:
    cycles: Counter[str] = Counter()
    gate_results: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    entry_styles: Counter[str] = Counter()
    gray_reasons: Counter[str] = Counter()
    margins: dict[str, list[float]] = {}
    latencies: list[float] = []
    prompt_bytes: list[int] = []
    prompt_tokens: list[int] = []
    would_call = 0
    decisions = 0
    errors = 0

    max_offset = len(bars) - window
    offsets = [i * step for i in range(samples) if i * step <= max_offset]
    for offset in offsets:
        frame = _window_frame(symbol, timeframe, bars, offset, window)
        started = time.perf_counter()
        try:
            outcome = decide(frame, band=band)
        except Exception:  # 单帧失败不污染整体统计
            errors += 1
            continue
        latencies.append((time.perf_counter() - started) * 1000.0)
        decision = outcome.decision
        decisions += 1
        cycles[str(decision.get("cycle_position"))] += 1
        gate_results[str(decision.get("gate_result"))] += 1
        outcomes[str((decision.get("terminal") or {}).get("outcome"))] += 1
        entry_styles[str(decision.get("entry_style"))] += 1

        gray = outcome.gray_zone
        if gray is not None and gray.need_llm:
            would_call += 1
            for reason in gray.reasons:
                gray_reasons[reason.split("（")[0][:60]] += 1
            prompt = build_gray_zone_prompt(
                frame,
                outcome.gate_results,
                gray,
                cycle_position=str(decision.get("cycle_position") or ""),
            )
            stats = prompt_size(prompt)
            prompt_bytes.append(int(stats["bytes"]))
            prompt_tokens.append(int(stats["estimated_tokens"]))
        measurements = measurements_from_results(
            outcome.gate_results, cycle_position=str(decision.get("cycle_position") or "")
        )
        for name, margin in contested_measurements(measurements, band=band):
            margins.setdefault(name, []).append(margin)

    margin_stats = {
        name: {
            "count": len(values),
            "min": round(min(values), 4),
            "p50": round(statistics.median(values), 4),
        }
        for name, values in sorted(margins.items())
    }
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "available_bars": len(bars),
        "windows": len(offsets),
        "decisions": decisions,
        "errors": errors,
        "gray_ratio": round(would_call / decisions, 4) if decisions else None,
        "cycles": dict(cycles),
        "gate_results": dict(gate_results),
        "outcomes": dict(outcomes),
        "entry_styles": dict(entry_styles),
        "gray_reasons": gray_reasons.most_common(8),
        "measurement_margins": margin_stats,
        "latency_ms": {
            "p50": round(_percentile(latencies, 0.5), 3) if latencies else None,
            "p95": round(_percentile(latencies, 0.95), 3) if latencies else None,
            "max": round(max(latencies), 3) if latencies else None,
        },
        "prompt_bytes": {
            "avg": round(statistics.mean(prompt_bytes), 1) if prompt_bytes else None,
            "max": max(prompt_bytes) if prompt_bytes else None,
            "limit": GZ.MAX_PROMPT_BYTES,
        },
        "est_tokens": {
            "avg": round(statistics.mean(prompt_tokens), 1) if prompt_tokens else None,
            "max": max(prompt_tokens) if prompt_tokens else None,
        },
    }


def _aggregate(series: list[dict[str, Any]]) -> dict[str, Any]:
    cycles: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    entry_styles: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    decisions = 0
    would_call = 0
    latencies: list[float] = []
    bytes_all: list[int] = []
    for item in series:
        decisions += item["decisions"]
        would_call += round((item["gray_ratio"] or 0) * item["decisions"])
        cycles.update(item["cycles"])
        outcomes.update(item["outcomes"])
        entry_styles.update(item["entry_styles"])
        reasons.update(dict(item["gray_reasons"]))
        if item["latency_ms"]["p50"] is not None:
            latencies.append(item["latency_ms"]["p50"])
        if item["prompt_bytes"]["avg"] is not None:
            bytes_all.append(item["prompt_bytes"]["avg"])
    return {
        "decisions": decisions,
        "gray_ratio": round(would_call / decisions, 4) if decisions else None,
        "cycles": dict(cycles),
        "outcomes": dict(outcomes),
        "entry_styles": dict(entry_styles),
        "gray_reasons": reasons.most_common(10),
        "latency_p50_avg": round(statistics.mean(latencies), 3) if latencies else None,
        "prompt_bytes_avg": round(statistics.mean(bytes_all), 1) if bytes_all else None,
    }


def _render_md(report: dict[str, Any]) -> str:
    agg = report["aggregate"]
    lines = [
        "# P-1 基线测量（真实行情 · 纯代码决策）",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 数据源：币安公共 K 线（免 key）；状态：{report['status']}",
        f"- 品种 × 周期：{report['series_count']} 条序列，{agg['decisions']} 个滑窗",
        f"- 灰区占比（would-call）：{agg['gray_ratio'] if agg['gray_ratio'] is not None else 'N/A'}",
        f"- 单帧延迟 p50 均值：{agg['latency_p50_avg']} ms",
        f"- 灰区 prompt 平均体积：{agg['prompt_bytes_avg']} bytes（上限 {GZ.MAX_PROMPT_BYTES}）",
        "",
        "## 汇总",
        "",
        f"- 周期分布：{agg['cycles']}",
        f"- 终态分布：{agg['outcomes']}",
        f"- 入场路径：{agg['entry_styles']}",
        f"- 灰区原因 Top：{agg['gray_reasons'][:5]}",
        "",
        "## 分序列",
        "",
        "| 品种 | 周期 | 窗口 | 灰区占比 | 延迟 p50/p95 | prompt 均/最大 | 周期分布 |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in report["series"]:
        lat = item["latency_ms"]
        pb = item["prompt_bytes"]
        lines.append(
            f"| {item['symbol']} | {item['timeframe']} | {item['decisions']} | "
            f"{item['gray_ratio'] if item['gray_ratio'] is not None else 'N/A'} | "
            f"{lat['p50']}/{lat['p95']} | {pb['avg']}/{pb['max']} | {item['cycles']} |"
        )
    if report.get("errors"):
        lines += ["", "## 降级 / 错误", ""]
        for error in report["errors"]:
            lines.append(f"- {error}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    parser.add_argument("--timeframes", default=DEFAULT_TIMEFRAMES)
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--window", type=int, default=100)
    parser.add_argument("--step", type=int, default=10)
    parser.add_argument("--band", type=float, default=GZ.BAND)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--json-out", default=str(DEFAULT_JSON))
    parser.add_argument("--md", default=str(DEFAULT_MD))
    args = parser.parse_args(argv)

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    timeframes = [item.strip() for item in args.timeframes.split(",") if item.strip()]
    need = args.window + args.samples * args.step + 5
    series: list[dict[str, Any]] = []
    errors: list[str] = []

    for symbol in symbols:
        for timeframe in timeframes:
            bars, source = _get_bars(
                symbol, timeframe, need, offline=args.offline, refresh=args.refresh
            )
            if not bars or len(bars) < args.window + 5:
                errors.append(f"{symbol} {timeframe}: {source}")
                continue
            item = _measure_series(
                symbol,
                timeframe,
                bars,
                samples=args.samples,
                window=args.window,
                step=args.step,
                band=args.band,
            )
            item["source"] = source
            series.append(item)
            print(
                f"{symbol} {timeframe}: windows={item['decisions']} "
                f"gray={item['gray_ratio']} p50={item['latency_ms']['p50']}ms"
            )

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "ok" if series else "degraded",
        "band": args.band,
        "window": args.window,
        "step": args.step,
        "series_count": len(series),
        "series": series,
        "aggregate": _aggregate(series),
        "errors": errors,
    }
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.md:
        path = Path(args.md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_md(report) + "\n", encoding="utf-8")
    print(f"-> {out}")
    if not series:
        print("没有可用序列：检查网络或使用 --refresh / --offline（需缓存）")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
