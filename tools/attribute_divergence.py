"""T3.2 分歧归因：把 v1/v2 差异逐条归类，产出可复现的审计报告。

归因类别：
- `v1_inconsistent`：v1 结论与其自身字段/终态矛盾（周期 unknown 却 proceed、
  有下单却非 trade、方向中性却下单等）；
- `v2_gap`：v2 关键节点缺失或未定位（周期 unknown、终态节点为空）；
- `threshold`：差异落在阈值边界带内（回放时记录的主判测量最小余量 ≤ BAND）；
- `other`：无法归入以上三类。

输入：
- `--replay-json`（默认 `records/replay/report.json`，由 replay_v1_records.py 产出）
- `--compare-json`（可选，由 compare_v1_v2.py 产出）
- `--records-dir`（灰区样本根，默认 `records`）

输出：
- `md/10_审计闭环报告.md` 的 `divergence` 区块
- `records/audit/divergence.json`

无样本时明确输出 N/A，不伪造归因比例。
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

from pa_engine.config.thresholds import GrayZone as GZ  # noqa: E402
from pa_engine.records.audit import DEFAULT_AUDIT_MD, upsert_section, write_json  # noqa: E402
from pa_engine.records.gray_samples import load_samples, summarize  # noqa: E402

CATEGORIES = ("v1_inconsistent", "v2_gap", "threshold", "other")
_NO_ORDER = {None, "", "不下单", "无", "none", "no_order", "NO_ORDER", "hold"}


def _has_order(order: Any) -> bool:
    return order not in _NO_ORDER


def _v1_conflict(expected: dict[str, Any]) -> str | None:
    """v1 自身字段互斥：这类差异不应归咎于 v2。"""
    cycle = str(expected.get("cycle_position") or "")
    gate = str(expected.get("gate_result") or "")
    direction = str(expected.get("direction") or "")
    always_in = str(expected.get("always_in") or "none")
    order = expected.get("order_type")
    outcome = str(expected.get("terminal_outcome") or "")

    if cycle == "unknown" and gate == "proceed":
        return "v1 周期=unknown 却给出 gate_result=proceed"
    if _has_order(order) and outcome not in ("trade", "action", "execute"):
        return f"v1 有下单（{order}）但终态={outcome or '空'}"
    if outcome == "trade" and not _has_order(order):
        return "v1 终态=trade 却无下单方式"
    if direction == "neutral" and always_in in ("bullish", "bearish") and _has_order(order):
        return f"v1 方向中性却下单（AlwaysIn={always_in}）"
    return None


def _v2_gap(actual: dict[str, Any]) -> str | None:
    if str(actual.get("cycle_position") or "unknown") == "unknown":
        return "v2 周期未定位"
    if not actual.get("terminal_node"):
        return "v2 终态节点为空"
    return None


def attribute_row(row: dict[str, Any], *, band: float = GZ.BAND) -> dict[str, Any]:
    """对一条回放行归因；无差异返回 `matched`。"""
    diffs = row.get("diffs") or {}
    base = {
        "record": row.get("record"),
        "fields": sorted(diffs),
        "min_margin": row.get("min_margin"),
        "min_margin_field": row.get("min_margin_field"),
    }
    if not diffs:
        return {**base, "category": "matched", "reason": "无字段差异"}

    reason = _v1_conflict(row.get("expected") or {})
    if reason:
        return {**base, "category": "v1_inconsistent", "reason": reason}

    reason = _v2_gap(row.get("actual") or {})
    if reason:
        return {**base, "category": "v2_gap", "reason": reason}

    margin = row.get("min_margin")
    if margin is not None and float(margin) <= float(band):
        return {
            **base,
            "category": "threshold",
            "reason": (
                f"主判测量 {row.get('min_margin_field')} 余量 {float(margin):.2%} "
                f"≤ band {float(band):.2%}，决策处于阈值边界"
            ),
        }

    return {
        **base,
        "category": "other",
        "reason": f"差异字段 {sorted(diffs)} 未落在边界带内",
    }


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _compare_attribution(compare: dict[str, Any]) -> dict[str, Any]:
    """v1/v2 特征层差异：统一归为 v2 实现差异（v2_gap）。"""
    items: list[dict[str, Any]] = []
    for name, result in (compare.get("results") or {}).items():
        for field, value in (result.get("diffs") or {}).items():
            items.append(
                {
                    "snapshot": name,
                    "field": field,
                    "v1": value.get("v1"),
                    "v2": value.get("v2"),
                    "category": "v2_gap",
                    "reason": "v2 特征实现与 v1 不一致",
                }
            )
    return {"present": True, "snapshots": compare.get("snapshots", 0), "items": items}


def build_report(
    *,
    replay: dict[str, Any] | None,
    compare: dict[str, Any] | None,
    samples: list[dict[str, Any]],
    band: float = GZ.BAND,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    counts = {name: 0 for name in CATEGORIES}
    diff_rows = 0
    if replay:
        for row in replay.get("rows") or []:
            item = attribute_row(row, band=band)
            if item["category"] == "matched":
                continue
            diff_rows += 1
            counts[item["category"]] = counts.get(item["category"], 0) + 1
            items.append(item)

    degraded: list[str] = []
    if not replay:
        degraded.append("回放报告缺失：分歧归因 N/A（先运行 tools/replay_v1_records.py）")
    elif not replay.get("rows"):
        degraded.append("回放报告无样本：归因 N/A")
    if compare:
        compare_part = _compare_attribution(compare)
    else:
        compare_part = {"present": False, "snapshots": 0, "items": []}
        degraded.append("v1/v2 特征对照缺失：先运行 tools/compare_v1_v2.py")

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "band": band,
        "status": "ok" if items or compare_part["items"] else "N/A",
        "replay": {
            "samples": (replay or {}).get("samples", 0),
            "replay_status": (replay or {}).get("status", "N/A"),
            "diff_rows": diff_rows,
            "attribution": counts,
            "items": items,
        },
        "compare": compare_part,
        "gray_samples": summarize(samples),
        "degraded": degraded,
    }


def _render_md(report: dict[str, Any]) -> str:
    replay = report["replay"]
    lines = [
        "## 1. 分歧归因（T3.2）",
        "",
        f"- 生成时间：{report['generated_at']}，band={report['band']:.2f}",
        f"- 回放样本：{replay['samples']}（状态：{replay['replay_status']}）",
        f"- 差异条数：{replay['diff_rows']}",
        "",
        "| 归因 | 条数 |",
        "| --- | ---: |",
    ]
    for name in CATEGORIES:
        lines.append(f"| {name} | {replay['attribution'].get(name, 0)} |")

    if replay["items"]:
        lines += ["", "### 逐条差异（最多 20 条）", ""]
        for item in replay["items"][:20]:
            lines.append(
                f"- `{item['record']}` → **{item['category']}**：{item['reason']}"
                f"（字段 {', '.join(item['fields'])}）"
            )
    else:
        lines += ["", "无回放差异可归因。"]

    lines += ["", "### v1/v2 特征对照", ""]
    compare_info = report["compare"]
    if compare_info["items"]:
        lines.append(
            f"- 快照 {compare_info['snapshots']} 个，差异字段 {len(compare_info['items'])} 项"
        )
        for item in compare_info["items"][:15]:
            lines.append(
                f"- `{item['snapshot']}.{item['field']}`：v1={item['v1']!r} vs v2={item['v2']!r}"
            )
    elif compare_info.get("present"):
        lines.append(f"- 快照 {compare_info['snapshots']} 个，字段全部一致（0 差异）")
    else:
        lines.append("- N/A（未提供 compare 报告）")

    samples = report["gray_samples"]
    lines += [
        "",
        "### 灰区样本库",
        "",
        f"- 样本数：{samples['samples']}，其中实际调用感知层：{samples['llm_called']}",
        f"- 后续走势：{samples['forward']}",
        f"- 争议节点 Top：{samples['contested_top'][:5]}",
        f"- prompt 字节 平均/最大：{samples['prompt_bytes']['avg']}/{samples['prompt_bytes']['max']}",
    ]
    if report["degraded"]:
        lines += ["", "### 降级说明", ""] + [f"- {item}" for item in report["degraded"]]
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-json", default=str(ROOT / "records" / "replay" / "report.json"))
    parser.add_argument("--compare-json", default=str(ROOT / "records" / "compare" / "report.json"))
    parser.add_argument("--records-dir", default=str(ROOT / "records"))
    parser.add_argument("--band", type=float, default=GZ.BAND)
    parser.add_argument("--md", default=str(ROOT / DEFAULT_AUDIT_MD))
    parser.add_argument("--json-out", default=str(ROOT / "records" / "audit" / "divergence.json"))
    args = parser.parse_args(argv)

    replay = _load_json(Path(args.replay_json))
    compare = _load_json(Path(args.compare_json))
    samples = load_samples(records_dir=args.records_dir)
    report = build_report(replay=replay, compare=compare, samples=samples, band=args.band)

    write_json(report, Path(args.json_out))
    upsert_section(Path(args.md), "divergence", _render_md(report))

    replay_info = report["replay"]
    print(
        f"samples={replay_info['samples']} diffs={replay_info['diff_rows']} "
        f"status={report['status']}"
    )
    for name in CATEGORIES:
        print(f"  {name:<16} {replay_info['attribution'].get(name, 0)}")
    print(f"gray_samples={report['gray_samples']['samples']}")
    print(f"-> {args.json_out}")
    print(f"-> {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
