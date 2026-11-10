"""灰区样本后续走势标注（T3.1 收尾）。

把 `records/gray_samples/*.jsonl` 中 `forward_outcome.status=pending` 的样本，
用锚点之后的已收盘 K 线补齐盈亏标签：

- `tp1` / `stop` / `both`（同一根双触，保守按止损）/ `open`（N 根内未触发）/ `no_plan`；
- 记录 bars_used、MFE/MAE（以 R 为单位）与最终 R 倍数。

数据来源（`--source`）：
- `auto`（默认）：records 中的后续快照 → 基线缓存 → 在线抓取；
- `records`：只读 `records/<日期>/*_<symbol>_<tf>.json` 的 `snapshot.bars`；
- `cache`：只读 `records/baseline/klines/<symbol>_<tf>.json`；
- `fetch`：调用币安公共历史接口（免鉴权）。

离线不可用时样本保持 pending，不猜测结果。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pa_engine.data.binance import KlineFetchError, fetch_klines_history  # noqa: E402
from pa_engine.data.types import KlineBar  # noqa: E402
from pa_engine.records.gray_samples import (  # noqa: E402
    FORWARD_BARS_DEFAULT,
    label_forward,
    load_samples,
    update_forward_outcome,
)

DEFAULT_RECORDS = ROOT / "records"
CACHE_DIR = ROOT / "records" / "baseline" / "klines"
FETCH_COUNT = 1000


def _bar_from_row(row: dict[str, Any], index: int) -> KlineBar | None:
    try:
        return KlineBar(
            seq=int(row.get("seq") or index),
            ts_open=float(row["ts_open"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume") or 0.0),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _bars_from_rows(rows: list[Any]) -> list[KlineBar]:
    bars = [
        bar
        for index, row in enumerate(rows, start=1)
        if isinstance(row, dict) and (bar := _bar_from_row(row, index)) is not None
    ]
    bars.sort(key=lambda bar: bar.ts_open, reverse=True)
    return bars


def _bars_from_records(root: Path, symbol: str, timeframe: str) -> list[KlineBar] | None:
    """取同品种同周期最新的 v2 记录快照（含 `snapshot.bars` 才可用）。"""
    best: list[KlineBar] | None = None
    best_ts = None
    for path in root.glob(f"*/*_{symbol}_{timeframe}.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = (payload.get("snapshot") or {}).get("bars")
        if not isinstance(rows, list):
            continue
        bars = _bars_from_rows(rows)
        if not bars:
            continue
        if best_ts is None or bars[0].ts_open > best_ts:
            best, best_ts = bars, bars[0].ts_open
    return best


def _bars_from_cache(symbol: str, timeframe: str) -> list[KlineBar] | None:
    path = CACHE_DIR / f"{symbol}_{timeframe}.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rows, list):
        return None
    return _bars_from_rows(rows) or None


def _bars_from_network(symbol: str, timeframe: str) -> tuple[list[KlineBar] | None, str]:
    try:
        bars = fetch_klines_history(symbol=symbol, timeframe=timeframe, count=FETCH_COUNT)
    except KlineFetchError as exc:
        return None, f"fetch-failed: {exc}"
    return bars, "network"


def resolve_bars(
    symbol: str,
    timeframe: str,
    *,
    root: Path,
    source: str,
    offline: bool,
) -> tuple[list[KlineBar] | None, str]:
    """按 source 顺序取 K 线，返回 (bars, 来源说明)。"""
    if source in ("auto", "records"):
        bars = _bars_from_records(root, symbol, timeframe)
        if bars:
            return bars, "records"
        if source == "records":
            return None, "records-empty"
    if source in ("auto", "cache"):
        bars = _bars_from_cache(symbol, timeframe)
        if bars:
            return bars, "cache"
        if source == "cache":
            return None, "cache-empty"
    if offline:
        return None, "offline"
    return _bars_from_network(symbol, timeframe)


def _later_bars(bars: list[KlineBar], anchor_ts: float) -> list[KlineBar]:
    newer = [bar for bar in bars if float(bar.ts_open) > float(anchor_ts)]
    return sorted(newer, key=lambda bar: float(bar.ts_open))


def label_samples(
    samples: list[dict[str, Any]],
    *,
    root: Path,
    source: str = "auto",
    offline: bool = False,
    forward_bars: int = FORWARD_BARS_DEFAULT,
    dry_run: bool = False,
) -> dict[str, Any]:
    """批量标注 pending 样本；返回统计与逐条结果。"""
    cache: dict[tuple[str, str], tuple[list[KlineBar] | None, str]] = {}
    labeled = 0
    pending = 0
    skipped = 0
    results: list[dict[str, Any]] = []
    notes: list[str] = []

    for sample in samples:
        forward = sample.get("forward_outcome") or {}
        if forward.get("status") not in (None, "", "pending"):
            continue
        symbol = str(sample.get("symbol") or "")
        timeframe = str(sample.get("timeframe") or "")
        anchor = sample.get("anchor_ts")
        if not symbol or not timeframe or anchor is None:
            pending += 1
            skipped += 1
            results.append({"sample_id": sample.get("sample_id"), "status": "skip", "reason": "缺少锚点"})
            continue

        key = (symbol, timeframe)
        if key not in cache:
            cache[key] = resolve_bars(
                symbol, timeframe, root=root, source=source, offline=offline
            )
        bars, origin = cache[key]
        if not bars:
            pending += 1
            results.append(
                {"sample_id": sample.get("sample_id"), "status": "skip", "reason": f"无K线（{origin}）"}
            )
            continue

        later = _later_bars(bars, float(anchor))
        if not later:
            pending += 1
            results.append(
                {"sample_id": sample.get("sample_id"), "status": "skip", "reason": "尚无后续K线"}
            )
            continue

        decision = sample.get("decision") or {}
        label = label_forward(decision, later, forward_bars=forward_bars)
        label["source"] = origin
        label["labeled_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = {
            "sample_id": sample.get("sample_id"),
            "status": label["status"],
            "bars_used": label.get("bars_used"),
            "r_multiple": label.get("r_multiple"),
        }
        if label["status"] == "no_plan":
            pending += 1
            results.append({**entry, "reason": "样本无完整下单方案"})
            continue

        if not dry_run:
            update_forward_outcome(
                str(sample.get("sample_id") or ""), label, records_dir=root
            )
        labeled += 1
        results.append(entry)
        notes.append(f"{symbol} {timeframe}: {label['status']}")

    return {
        "samples": len(samples),
        "labeled": labeled,
        "pending": pending,
        "skipped": skipped,
        "dry_run": dry_run,
        "results": results,
        "notes": notes[:20],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--records-dir", default=str(DEFAULT_RECORDS))
    parser.add_argument("--source", choices=("auto", "records", "cache", "fetch"), default="auto")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--forward-bars", type=int, default=FORWARD_BARS_DEFAULT)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json-out", default=str(ROOT / "records" / "audit" / "labeling.json"))
    args = parser.parse_args(argv)

    root = Path(args.records_dir)
    samples = load_samples(records_dir=root, limit=args.limit)
    if not samples:
        print("samples=0：样本库为空（灰区分析后由 records/writer.py 自动落盘）")
        return 0

    report = label_samples(
        samples,
        root=root,
        source=args.source,
        offline=args.offline,
        forward_bars=args.forward_bars,
        dry_run=args.dry_run,
    )
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"samples={report['samples']} labeled={report['labeled']} "
        f"pending={report['pending']} skipped={report['skipped']} dry_run={report['dry_run']}"
    )
    for note in report["notes"]:
        print(f"  {note}")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
