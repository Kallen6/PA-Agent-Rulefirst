"""v1 AnalysisRecord 只读适配：重建 v2 帧、抽取可比较字段。

v1 记录（PA_Agent/records/pending/*.json）结构：
    meta / kline_data / stage1_diagnosis / stage2_decision / ...

本模块仅读取，不导入 v1 orchestrator / prompt_assembler；v2 运行时无需 v1 在场。
"""
from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Any

from pa_engine.data.types import KlineBar, KlineFrame, build_frame

_DIRECTION_MAP = {
    "long": "bullish",
    "short": "bearish",
    "bullish": "bullish",
    "bearish": "bearish",
    "neutral": "neutral",
    "中性": "neutral",
    "做多": "bullish",
    "做空": "bearish",
}
_OUTCOME_MAP = {"action": "trade", "execute": "trade", "no_trade": "wait"}


def load_record(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"记录不是 JSON 对象：{path}")
    return data


def _bar_dict(bar: Any) -> dict[str, Any]:
    if isinstance(bar, dict):
        return bar
    if hasattr(bar, "__dict__"):
        return {k: v for k, v in bar.__dict__.items()}
    if hasattr(bar, "__dataclass_fields__"):
        return {f.name: getattr(bar, f.name) for f in fields(bar)}
    raise TypeError(f"无法序列化 K 线：{bar!r}")


def frame_from_record(record: dict[str, Any]) -> KlineFrame:
    raw = record.get("kline_data") or []
    if len(raw) < 2:
        raise ValueError("kline_data 不足 2 根，无法重建帧")
    rows = [_bar_dict(item) for item in raw]
    # v1 习惯 newest-first；若时间升序则反转，统一成 newest-first。
    first_ts = float(rows[0].get("ts_open") or 0)
    last_ts = float(rows[-1].get("ts_open") or 0)
    if first_ts < last_ts:
        rows = list(reversed(rows))
    bars = [
        KlineBar(
            seq=int(row.get("seq") or i),
            ts_open=float(row["ts_open"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume") or 0.0),
        )
        for i, row in enumerate(rows, start=1)
    ]
    meta = record.get("meta") or {}
    return build_frame(
        str(meta.get("symbol") or "UNKNOWN"),
        str(meta.get("timeframe") or "1h"),
        bars,
        snapshot_ts_local_ms=int(meta.get("timestamp_local_ms") or 0),
    )


def stage1_fields(record: dict[str, Any]) -> dict[str, Any]:
    st1 = record.get("stage1_diagnosis") or {}
    bar_analysis = st1.get("bar_analysis") or {}
    always_in = _DIRECTION_MAP.get(
        str(bar_analysis.get("always_in") or "").strip().lower(), "none"
    )
    return {
        "cycle_position": str(st1.get("cycle_position") or "unknown"),
        "direction": _DIRECTION_MAP.get(
            str(st1.get("direction") or "").strip().lower(), "neutral"
        ),
        "always_in": always_in,
        "spike_stage": st1.get("spike_stage"),
        "gate_result": str(st1.get("gate_result") or "unknown"),
        "diagnosis_confidence": st1.get("diagnosis_confidence"),
    }


def stage2_fields(record: dict[str, Any]) -> dict[str, Any]:
    st2 = record.get("stage2_decision") or {}
    decision = st2.get("decision") if isinstance(st2.get("decision"), dict) else st2
    terminal = st2.get("terminal") if isinstance(st2.get("terminal"), dict) else {}
    outcome = str(terminal.get("outcome") or "wait")
    outcome = _OUTCOME_MAP.get(outcome, outcome)
    return {
        "order_type": str(decision.get("order_type") or "不下单"),
        "order_direction": decision.get("order_direction"),
        "entry_price": _to_float(decision.get("entry_price")),
        "stop_loss_price": _to_float(decision.get("stop_loss_price")),
        "take_profit_price": _to_float(decision.get("take_profit_price")),
        "take_profit_price_2": _to_float(decision.get("take_profit_price_2")),
        "terminal_node": str(terminal.get("node_id") or ""),
        "terminal_outcome": outcome,
    }


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compare_fields(
    actual: dict[str, Any],
    expected: dict[str, Any],
    *,
    fields: tuple[str, ...],
    price_fields: tuple[str, ...] = (),
    tolerance_pct: float = 0.002,
    tick: float = 0.01,
) -> dict[str, Any]:
    """逐字段比较，返回 {field: {"ok": bool, "actual":..., "expected":...}}。"""
    diff: dict[str, Any] = {}
    for name in fields:
        a, e = actual.get(name), expected.get(name)
        if isinstance(a, str) or isinstance(e, str):
            ok = str(a or "") == str(e or "")
        else:
            ok = a == e
        if not ok:
            diff[name] = {"ok": False, "actual": a, "expected": e}
    for name in price_fields:
        a, e = actual.get(name), expected.get(name)
        if a is None or e is None:
            ok = a is None and e is None
        else:
            tol = max(abs(float(e)) * tolerance_pct, tick * 2)
            ok = abs(float(a) - float(e)) <= tol
        if not ok:
            diff[name] = {"ok": False, "actual": a, "expected": e}
    return diff
