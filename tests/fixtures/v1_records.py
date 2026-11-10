"""v1 schema 记录夹具：用于回放工具自测（不依赖真实 v1 记录）。"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pa_engine.data.types import KlineFrame
from pa_engine.engine.pipeline import decide

_ALWAYS_IN_BACK = {"bullish": "long", "bearish": "short", "none": "neutral", "neutral": "neutral"}


def record_from_frame(frame: KlineFrame, decision: dict[str, Any] | None = None) -> dict:
    """根据 v2 决策生成一份 v1 schema 记录，供回放自测使用。

    `decision` 省略时现场跑一次纯代码决策。
    """
    decision = decision or decide(frame).decision
    gate_result = decision.get("gate_result", "proceed")
    stage1 = {
        "cycle_position": decision["cycle_position"],
        "direction": decision["direction"],
        "diagnosis_confidence": decision.get("diagnosis_confidence", 70),
        "spike_stage": decision.get("spike_stage"),
        "bar_analysis": {
            "always_in": _ALWAYS_IN_BACK.get(decision.get("always_in", "none"), "neutral"),
            "last_closed_bar": "K1",
            "bar_type": "other",
        },
        "gate_result": gate_result,
        "gate_trace": [],
    }
    terminal = decision.get("terminal") or {}
    stage2 = {
        "decision": {
            "order_type": decision.get("order_type", "不下单"),
            "order_direction": decision.get("order_direction"),
            "entry_price": decision.get("entry_price"),
            "stop_loss_price": decision.get("stop_loss_price"),
            "take_profit_price": decision.get("take_profit_price"),
            "take_profit_price_2": decision.get("take_profit_price_2"),
        },
        "diagnosis_summary": {
            "cycle_position": decision["cycle_position"],
            "direction": decision["direction"],
        },
        "terminal": {
            "node_id": terminal.get("node_id", ""),
            "outcome": terminal.get("outcome", "wait"),
            "label": terminal.get("label", ""),
        },
        "decision_trace": decision.get("decision_trace", []),
    }
    return {
        "meta": {
            "timestamp_local_iso": "2026-01-01T00:00:00.000",
            "timestamp_local_ms": 1_700_000_000_000,
            "symbol": frame.symbol,
            "timeframe": frame.timeframe,
            "bar_count": len(frame.bars),
            "ai_provider": {},
            "decision_stance": "conservative",
        },
        "kline_data": [asdict(bar) for bar in frame.bars],
        "htf_text": "",
        "stage1_messages": [],
        "stage1_response": None,
        "stage1_diagnosis": stage1,
        "stage2_messages": [],
        "stage2_response": None,
        "stage2_decision": stage2,
        "strategy_files_used": [],
        "experience_loaded": [],
        "exception": None,
        "usage_total": {},
    }


def broken_record(frame: KlineFrame, *, missing_stage2: bool = False) -> dict:
    record = record_from_frame(frame)
    if missing_stage2:
        record["stage2_decision"] = None
    else:
        record["stage1_diagnosis"] = None
    return record
