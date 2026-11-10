"""T1.7 灰区带宽扫描与工作点选择。

- 在同一批真实行情滑窗上扫描 BAND；
- 统计灰区覆盖率、相对 band=0 的决策翻转率、调用占比；
- 选择灰区占比 20%–40%、翻转率 ≤2% 的最小带宽；
- `--write` 把结果写回 `pa_engine/config/thresholds.py`（唯一来源）。

用法：
    python tools/calibrate_band.py
    python tools/calibrate_band.py --write --md md/08_带宽标定.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pa_engine.config.thresholds import GrayZone as GZ  # noqa: E402
from pa_engine.data.types import build_frame  # noqa: E402
from pa_engine.engine.pipeline import decide  # noqa: E402
from tools.baseline_measure import (  # noqa: E402
    DEFAULT_SYMBOLS,
    DEFAULT_TIMEFRAMES,
    _get_bars,
)

CANDIDATES = (0.05, 0.08, 0.10, 0.12, 0.15, 0.20)
TARGET_MIN = 0.20
TARGET_MAX = 0.40
MAX_FLIP = 0.02
FALLBACK_FLIP = 0.05
THRESHOLDS_FILE = ROOT / "pa_engine" / "config" / "thresholds.py"
DEFAULT_MD = ROOT / "md" / "08_带宽标定.md"
DEFAULT_JSON = ROOT / "records" / "baseline" / "band_scan.json"


def _frames(
    symbols: list[str],
    timeframes: list[str],
    *,
    samples: int,
    window: int,
    step: int,
    offline: bool,
) -> tuple[list[Any], list[str]]:
    need = window + samples * step + 5
    frames: list[Any] = []
    errors: list[str] = []
    for symbol in symbols:
        for timeframe in timeframes:
            bars, source = _get_bars(
                symbol, timeframe, need, offline=offline, refresh=False
            )
            if not bars or len(bars) < window + 5:
                errors.append(f"{symbol} {timeframe}: {source}")
                continue
            max_offset = len(bars) - window
            for index in range(samples):
                offset = index * step
                if offset > max_offset:
                    break
                chunk = bars[offset : offset + window]
                reseq = [replace(bar, seq=i) for i, bar in enumerate(chunk, start=1)]
                frames.append(build_frame(symbol, timeframe, reseq))
    return frames, errors


def _signature(decision: dict[str, Any]) -> tuple:
    terminal = decision.get("terminal") or {}
    return (
        decision.get("cycle_position"),
        decision.get("gate_result"),
        decision.get("entry_style"),
        decision.get("order_type"),
        decision.get("order_direction"),
        round(float(decision["entry_price"]), 8) if decision.get("entry_price") else None,
        round(float(decision["stop_loss_price"]), 8) if decision.get("stop_loss_price") else None,
        round(float(decision["take_profit_price"]), 8) if decision.get("take_profit_price") else None,
        terminal.get("node_id"),
        terminal.get("outcome"),
    )


def scan(
    frames: list[Any],
    *,
    candidates: tuple[float, ...] = CANDIDATES,
) -> dict[str, Any]:
    baselines = [_signature(decide(frame, band=0.0).decision) for frame in frames]
    rows = []
    for band in candidates:
        started = time.perf_counter()
        signatures = []
        gray = 0
        for frame in frames:
            outcome = decide(frame, band=band)
            signatures.append(_signature(outcome.decision))
            if outcome.gray_zone is not None and outcome.gray_zone.need_llm:
                gray += 1
        flips = sum(1 for a, b in zip(signatures, baselines) if a != b)
        rows.append(
            {
                "band": band,
                "gray_ratio": round(gray / len(frames), 4) if frames else None,
                "flip_ratio": round(flips / len(frames), 4) if frames else None,
                "flips": flips,
                "latency_ms": round((time.perf_counter() - started) * 1000 / max(len(frames), 1), 3),
            }
        )

    golden = [
        row
        for row in rows
        if row["gray_ratio"] is not None
        and TARGET_MIN <= row["gray_ratio"] <= TARGET_MAX
        and row["flip_ratio"] <= MAX_FLIP
    ]
    if golden:
        selected = min(golden, key=lambda row: row["band"])
        rule = "target_20_40_flip_le_2"
    else:
        relaxed = [row for row in rows if row["flip_ratio"] <= FALLBACK_FLIP]
        pool = relaxed or rows
        selected = min(pool, key=lambda row: abs((row["gray_ratio"] or 0) - 0.30))
        rule = "fallback_closest_30_flip_le_5" if relaxed else "fallback_best_effort"
    return {"selected": selected, "rule": rule, "rows": rows, "samples": len(frames)}


def _write_band(value: float) -> None:
    text = THRESHOLDS_FILE.read_text(encoding="utf-8")
    updated, count = re.subn(
        r"(?m)^(\s*)BAND = [0-9.]+(\s*#.*)?$",
        rf"\g<1>BAND = {value:.2f}\g<2>",
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError("未找到唯一 GrayZone.BAND 定义")
    THRESHOLDS_FILE.write_text(updated, encoding="utf-8")


def _render_md(report: dict[str, Any], *, written: float | None) -> str:
    selected = report["selected"]
    lines = [
        "# 灰区带宽标定（BAND）",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 样本窗口：{report['samples']}（真实行情，币安公共 K 线）",
        f"- 当前 BAND：{report['current_band']:.2f}",
        f"- 选定 BAND：{selected['band']:.2f}（规则：{report['rule']}）",
        f"- 灰区占比：{selected['gray_ratio']:.2%}，相对 band=0 翻转率：{selected['flip_ratio']:.2%}",
        f"- 写回 thresholds.py：{'是' if written is not None else '否（只读）'}",
        "",
        "## 扫描表",
        "",
        "| BAND | 灰区占比 | 翻转率 | 翻转数 | 单帧 ms |",
        "| ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report["rows"]:
        lines.append(
            f"| {row['band']:.2f} | {row['gray_ratio']:.2%} | {row['flip_ratio']:.2%} | "
            f"{row['flips']} | {row['latency_ms']} |"
        )
    lines += [
        "",
        "## 说明",
        "",
        "- 灰区占比目标 20%–40%；翻转率指该 BAND 下决策签名与 band=0 的差异比例。",
        "- 选择规则：先取目标区间内翻转率 ≤2% 的最小带宽；不可达时取最接近 30% 且翻转率 ≤5% 的工作点。",
        "",
    ]
    if report.get("errors"):
        lines += ["### 降级序列", ""] + [f"- {e}" for e in report["errors"]] + [""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    parser.add_argument("--timeframes", default=DEFAULT_TIMEFRAMES)
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--window", type=int, default=100)
    parser.add_argument("--step", type=int, default=10)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--write", action="store_true", help="写回 thresholds.py")
    parser.add_argument("--md", default=str(DEFAULT_MD))
    parser.add_argument("--json-out", default=str(DEFAULT_JSON))
    args = parser.parse_args(argv)

    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    timeframes = [item.strip() for item in args.timeframes.split(",") if item.strip()]
    frames, errors = _frames(
        symbols,
        timeframes,
        samples=args.samples,
        window=args.window,
        step=args.step,
        offline=args.offline,
    )
    if not frames:
        print("没有可用样本：先运行 tools/baseline_measure.py 生成缓存")
        return 1
    result = scan(frames)
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "current_band": GZ.BAND,
        "samples": len(frames),
        "errors": errors,
        **result,
    }
    written = None
    if args.write:
        written = float(result["selected"]["band"])
        _write_band(written)
        report["written_band"] = written
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.md:
        path = Path(args.md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_md(report, written=written) + "\n", encoding="utf-8")

    selected = result["selected"]
    print(
        f"samples={len(frames)} selected_band={selected['band']:.2f} "
        f"gray={selected['gray_ratio']:.2%} flip={selected['flip_ratio']:.2%} rule={result['rule']}"
    )
    for row in result["rows"]:
        print(
            f"  band={row['band']:.2f} gray={row['gray_ratio']:.2%} "
            f"flip={row['flip_ratio']:.2%} flips={row['flips']}"
        )
    if written is not None:
        print(f"写好 thresholds.GrayZone.BAND = {written:.2f}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
