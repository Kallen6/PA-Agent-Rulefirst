"""T3.4 带宽与阈值季度复核（市场非平稳，防止阈值过时）。

- 复用基线滑窗与带宽扫描逻辑，比较「当前 BAND」与「建议 BAND」；
- 漂移超过一个候选步长、灰区占比越界、翻转率超限时输出告警；
- **默认只读**，不自动改阈值；确认后由 `tools/calibrate_band.py --write` 写回。

用法：
    python tools/quarterly_review.py
    python tools/quarterly_review.py --offline --samples 30
    python tools/quarterly_review.py --md md/10_审计闭环报告.md
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pa_engine.config.thresholds import GrayZone as GZ  # noqa: E402
from pa_engine.records.audit import DEFAULT_AUDIT_MD, upsert_section, write_json  # noqa: E402
from tools.baseline_measure import DEFAULT_SYMBOLS, DEFAULT_TIMEFRAMES  # noqa: E402
from tools.calibrate_band import (  # noqa: E402
    CANDIDATES,
    MAX_FLIP,
    TARGET_MAX,
    TARGET_MIN,
    _frames,
    scan,
)

DRIFT_ALERT = 0.02          # 一个候选步长（0.05→0.08 之间的最小差距）
GRAY_LOW_ALERT = TARGET_MIN
GRAY_HIGH_ALERT = TARGET_MAX


def review(frames: list[Any], *, current_band: float) -> dict[str, Any]:
    """扫描并比较当前工作点，返回漂移告警与建议值。"""
    candidates = tuple(sorted({float(value) for value in CANDIDATES} | {float(current_band)}))
    result = scan(frames, candidates=candidates)
    selected = result["selected"]
    recommended = float(selected["band"])
    drift = round(recommended - float(current_band), 4)
    current_row = next(
        (row for row in result["rows"] if abs(float(row["band"]) - float(current_band)) < 1e-9),
        None,
    )

    alerts: list[str] = []
    if current_row is None:
        alerts.append("当前 BAND 未出现在扫描表中，无法评估")
    if abs(drift) >= DRIFT_ALERT:
        alerts.append(
            f"建议工作点漂移 {drift:+.2f}（当前 {current_band:.2f} → 建议 {recommended:.2f}）"
        )
    if current_row and current_row["gray_ratio"] is not None:
        gray = float(current_row["gray_ratio"])
        if gray < GRAY_LOW_ALERT:
            alerts.append(f"当前灰区占比 {gray:.2%} 低于目标下限 {GRAY_LOW_ALERT:.0%}")
        elif gray > GRAY_HIGH_ALERT:
            alerts.append(f"当前灰区占比 {gray:.2%} 高于目标上限 {GRAY_HIGH_ALERT:.0%}")
        if float(current_row["flip_ratio"] or 0) > MAX_FLIP:
            alerts.append(
                f"当前翻转率 {float(current_row['flip_ratio']):.2%} 超过 {MAX_FLIP:.0%}"
            )

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "samples": len(frames),
        "current_band": float(current_band),
        "recommended_band": recommended,
        "drift": drift,
        "rule": result["rule"],
        "status": "drift" if alerts else "stable",
        "alerts": alerts,
        "current": current_row,
        "rows": result["rows"],
    }


def _render_md(report: dict[str, Any]) -> str:
    current = report.get("current") or {}
    lines = [
        "## 2. 季度阈值复核（T3.4）",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 样本窗口：{report['samples']}（真实行情滑窗，纯代码决策）",
        f"- 当前 BAND：{report['current_band']:.2f} → 建议 BAND：{report['recommended_band']:.2f}"
        f"（漂移 {report['drift']:+.2f}，规则 {report['rule']}）",
        f"- 状态：**{report['status']}**",
        "",
        "| 指标 | 当前工作点 |",
        "| --- | ---: |",
        f"| 灰区占比 | {_pct(current.get('gray_ratio'))} |",
        f"| 翻转率 | {_pct(current.get('flip_ratio'))} |",
        f"| 单帧 ms | {current.get('latency_ms', 'N/A')} |",
        "",
        "### 扫描表",
        "",
        "| BAND | 灰区占比 | 翻转率 | 翻转数 |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for row in report["rows"]:
        lines.append(
            f"| {row['band']:.2f} | {_pct(row['gray_ratio'])} | {_pct(row['flip_ratio'])} | {row['flips']} |"
        )
    if report["alerts"]:
        lines += ["", "### 漂移告警", ""] + [f"- {item}" for item in report["alerts"]]
    else:
        lines += ["", "无漂移告警：当前工作点仍在目标区间内。"]
    lines += [
        "",
        "> 本工具默认只读。确认后执行 "
        "`python tools/calibrate_band.py --write` 写回 `thresholds.GrayZone.BAND`。",
        "",
    ]
    return "\n".join(lines)


def _pct(value: Any) -> str:
    return f"{float(value):.2%}" if value is not None else "N/A"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    parser.add_argument("--timeframes", default=DEFAULT_TIMEFRAMES)
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--window", type=int, default=100)
    parser.add_argument("--step", type=int, default=10)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--band", type=float, default=GZ.BAND, help="当前工作点（默认取 thresholds）")
    parser.add_argument("--md", default=str(ROOT / DEFAULT_AUDIT_MD))
    parser.add_argument("--json-out", default=str(ROOT / "records" / "audit" / "quarterly_review.json"))
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
        print("没有可用样本：先运行 tools/baseline_measure.py 生成缓存，或去掉 --offline")
        return 1

    report = review(frames, current_band=args.band)
    report["errors"] = errors
    write_json(report, Path(args.json_out))
    upsert_section(Path(args.md), "quarterly", _render_md(report))

    print(
        f"samples={report['samples']} current={report['current_band']:.2f} "
        f"recommended={report['recommended_band']:.2f} drift={report['drift']:+.2f} "
        f"status={report['status']}"
    )
    for alert in report["alerts"]:
        print(f"  ! {alert}")
    print(f"-> {args.json_out}")
    print(f"-> {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
