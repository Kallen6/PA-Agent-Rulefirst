"""v1 历史记录回放：逐条复算 v2 决策并输出字段级一致率（T0.6/T1.5）。

用法：
    python tools/replay_v1_records.py                 # 默认 PA_Agent/records/pending
    python tools/replay_v1_records.py --records DIR --limit 100
    python tools/replay_v1_records.py --self-test     # 用夹具验证解析/比对/统计
    python tools/replay_v1_records.py --md md/09_回放与一致性报告.md

约定：
- v1 记录为空 → `samples=0, status=N/A`，退出码 0，不伪造一致率；
- 价格字段容差 = max(参考价×0.2%, 2跳)；
- 只读 v1 记录，不修改原项目。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT.parent
sys.path.insert(0, str(ROOT))

from pa_engine.adapters.v1_records import (  # noqa: E402
    compare_fields,
    frame_from_record,
    load_record,
    stage1_fields,
    stage2_fields,
)
from pa_engine.config.thresholds import GrayZone as GZ  # noqa: E402
from pa_engine.engine.gray_zone import (  # noqa: E402
    closest_margin,
    measurements_from_results,
)
from pa_engine.engine.pipeline import decide  # noqa: E402

EXACT_FIELDS = (
    "cycle_position",
    "direction",
    "always_in",
    "gate_result",
    "order_type",
    "terminal_node",
    "terminal_outcome",
)
PRICE_FIELDS = (
    "entry_price",
    "stop_loss_price",
    "take_profit_price",
    "take_profit_price_2",
)
DEFAULT_RECORDS = WORKSPACE / "PA_Agent" / "records" / "pending"
DEFAULT_JSON = ROOT / "records" / "replay" / "report.json"


def _actual_fields(decision: dict[str, Any]) -> dict[str, Any]:
    always_in = str(decision.get("always_in") or "none")
    if always_in == "none":
        always_in = "neutral"
    terminal = decision.get("terminal") or {}
    return {
        "cycle_position": decision.get("cycle_position"),
        "direction": decision.get("direction"),
        "always_in": always_in,
        "gate_result": decision.get("gate_result"),
        "order_type": decision.get("order_type"),
        "terminal_node": terminal.get("node_id"),
        "terminal_outcome": terminal.get("outcome"),
        "entry_price": decision.get("entry_price"),
        "stop_loss_price": decision.get("stop_loss_price"),
        "take_profit_price": decision.get("take_profit_price"),
        "take_profit_price_2": decision.get("take_profit_price_2"),
        "spike_stage": decision.get("spike_stage"),
    }


def compare_record(
    record: dict[str, Any], *, tolerance_pct: float = 0.002, band: float | None = None
) -> dict[str, Any]:
    frame = frame_from_record(record)
    outcome = decide(frame)
    decision = outcome.decision
    actual = _actual_fields(decision)
    expected = {**stage1_fields(record), **stage2_fields(record)}
    diff = compare_fields(
        actual,
        expected,
        fields=EXACT_FIELDS,
        price_fields=PRICE_FIELDS,
        tolerance_pct=tolerance_pct,
    )
    if expected.get("spike_stage") is not None or actual.get("spike_stage") is not None:
        spike_diff = compare_fields(
            actual, expected, fields=("spike_stage",), tolerance_pct=tolerance_pct
        )
        diff.update(spike_diff)
    # 距阈值最近的测量：供分歧归因判断「差异是否落在阈值边界」
    measurements = measurements_from_results(
        outcome.gate_results, cycle_position=str(decision.get("cycle_position") or "")
    )
    margin = closest_margin(measurements)
    return {
        "actual": actual,
        "expected": expected,
        "diffs": diff,
        "matched": len(diff) == 0,
        "min_margin": margin[1] if margin else None,
        "min_margin_field": margin[0] if margin else None,
        "band": float(GZ.BAND if band is None else band),
    }


def replay_records(records: Iterable[dict[str, Any]], *, tolerance_pct: float = 0.002) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        name = str((record.get("meta") or {}).get("timestamp_local_iso") or f"record-{index}")
        try:
            rows.append({"record": name, **compare_record(record, tolerance_pct=tolerance_pct)})
        except Exception as exc:  # 单条坏记录不阻断整体
            errors.append({"record": name, "error": f"{type(exc).__name__}: {exc}"})

    field_stats: dict[str, dict[str, float | int]] = {}
    for field in EXACT_FIELDS + PRICE_FIELDS + ("spike_stage",):
        values = [row for row in rows if field in row["actual"] or field in row["expected"]]
        if not values:
            continue
        matched = sum(1 for row in values if field not in row["diffs"])
        field_stats[field] = {
            "matched": matched,
            "total": len(values),
            "rate": round(matched / len(values), 4),
        }
    total_slots = sum(int(v["total"]) for v in field_stats.values())
    total_matched = sum(int(v["matched"]) for v in field_stats.values())
    na_fields: dict[str, int] = {}
    for row in rows:
        for field, value in row["expected"].items():
            if value is None or value == "":
                na_fields[field] = na_fields.get(field, 0) + 1
    margins = [row["min_margin"] for row in rows if row.get("min_margin") is not None]
    return {
        "status": "ok" if rows else "N/A",
        "samples": len(rows),
        "errors": errors,
        "overall_rate": round(total_matched / total_slots, 4) if total_slots else None,
        "field_stats": field_stats,
        "na_fields": na_fields,
        "margin_stats": {
            "count": len(margins),
            "min": round(min(margins), 4) if margins else None,
            "median": round(sorted(margins)[len(margins) // 2], 4) if margins else None,
        },
        "rows": rows,
    }


def _records_from_dir(records_dir: Path, limit: int | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(records_dir.glob("*.json")):
        try:
            records.append(load_record(path))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if limit is not None and len(records) >= limit:
            break
    return records


def _write_json(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _render_md(report: dict[str, Any], *, records_dir: Path) -> str:
    lines = [
        "## 2. v1 记录回放",
        "",
        f"- 样本目录：`{records_dir}`",
        f"- 样本数：{report['samples']}，坏记录：{len(report['errors'])}",
        f"- 总体字段一致率：{report['overall_rate'] if report['overall_rate'] is not None else 'N/A'}",
        f"- 状态：{report['status']}",
        "",
        "### 字段级一致率",
        "",
        "| 字段 | 匹配 | 总数 | 一致率 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for field, stat in report["field_stats"].items():
        lines.append(f"| {field} | {stat['matched']} | {stat['total']} | {stat['rate']:.2%} |")
    if report["errors"]:
        lines += ["", "### 坏记录", ""]
        for item in report["errors"][:20]:
            lines.append(f"- `{item['record']}`：{item['error']}")
    diffs = [row for row in report["rows"] if row["diffs"]]
    if diffs:
        lines += ["", "### 逐条差异（最多 20 条）", ""]
        for row in diffs[:20]:
            lines.append(f"### {row['record']}")
            for field, diff in row["diffs"].items():
                lines.append(
                    f"- `{field}`：v2={diff['actual']!r} vs v1={diff['expected']!r}"
                )
    else:
        lines += ["", "无字段差异。"]
    lines.append("")
    return "\n".join(lines)


def _self_test() -> int:
    from tests.fixtures.synthetic import make_regime_rows, make_setup_rows
    from tests.fixtures.v1_records import record_from_frame
    from tests.support import build_v2_frame

    frames = [
        build_v2_frame(make_regime_rows("tight_channel")),
        build_v2_frame(make_setup_rows("broad_channel")),
    ]
    report = replay_records([record_from_frame(frame) for frame in frames])
    ok = report["samples"] == 2 and report["overall_rate"] == 1.0 and not report["errors"]
    print(f"self-test: samples={report['samples']} rate={report['overall_rate']} -> {'PASS' if ok else 'FAIL'}")
    if not ok:
        for row in report["rows"]:
            if row["diffs"]:
                print(row["record"], row["diffs"])
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", default=str(DEFAULT_RECORDS), help="v1 records/pending 目录")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json-out", default=str(DEFAULT_JSON))
    parser.add_argument("--md", default=None, help="额外输出 Markdown 报告")
    parser.add_argument("--tolerance-pct", type=float, default=0.002)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    records_dir = Path(args.records)
    if not records_dir.is_dir():
        # 无样本不是错误：显式输出 N/A，绝不伪造一致率
        empty = {
            "status": "N/A",
            "samples": 0,
            "errors": [],
            "overall_rate": None,
            "field_stats": {},
            "na_fields": {},
            "rows": [],
        }
        _write_json(empty, Path(args.json_out))
        print(f"samples=0 status=N/A（{records_dir} 不存在，无 v1 记录）")
        if args.md:
            from pa_engine.records.audit import upsert_section

            upsert_section(
                Path(args.md),
                "replay",
                _render_md(empty, records_dir=records_dir),
                title="# 09 回放与一致性报告",
            )
        return 0
    records = _records_from_dir(records_dir, args.limit)
    report = replay_records(records, tolerance_pct=args.tolerance_pct)
    _write_json(report, Path(args.json_out))
    if args.md:
        from pa_engine.records.audit import upsert_section

        upsert_section(
            Path(args.md),
            "replay",
            _render_md(report, records_dir=records_dir),
            title="# 09 回放与一致性报告",
        )
    if report["status"] == "N/A":
        print(f"samples=0 status=N/A（{records_dir} 无可用记录）")
        print("提示：需先在 v1 跑一批历史样本；本工具不会伪造一致率。")
        return 0
    print(
        f"samples={report['samples']} overall={report['overall_rate']:.2%} "
        f"errors={len(report['errors'])} -> {args.json_out}"
    )
    for field, stat in report["field_stats"].items():
        print(f"  {field:<22} {stat['rate']:.2%} ({stat['matched']}/{stat['total']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
