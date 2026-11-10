"""灰区样本库单测：追加/去重、脱敏、走势标注、统计、writer 联动。"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pa_engine.engine.gray_zone import GrayZoneVerdict
from pa_engine.engine.pipeline import DecisionOutcome
from pa_engine.gates.base import GateResult
from pa_engine.records.gray_samples import (
    append_sample,
    build_sample,
    label_forward,
    load_samples,
    summarize,
    update_forward_outcome,
)
from pa_engine.records.writer import save_decision


@dataclass
class _Bar:
    high: float
    low: float
    close: float


def _outcome(*, cycle: str = "tight_channel") -> DecisionOutcome:
    decision = {
        "cycle_position": cycle,
        "direction": "bullish",
        "always_in": "bullish",
        "gate_result": "proceed",
        "entry_style": "background_limit",
        "order_type": "限价单",
        "order_direction": "做多",
        "entry_price": 100.0,
        "stop_loss_price": 98.0,
        "take_profit_price": 104.0,
        "take_profit_price_2": 108.0,
        "estimated_win_rate": 58.0,
        "diagnosis_confidence": 82.0,
        "terminal": {"node_id": "11.4", "outcome": "trade", "label": "限价单"},
        "patterns": ["triangle"],
        "reversal": {},
        "wedge": {},
    }
    return DecisionOutcome(
        decision=decision,
        stage1={"cycle_position": cycle},
        gate_results=[
            GateResult("5.2", True, "tight_channel", 0.6, {"pullback_pct": 0.29}, "边界"),
            GateResult("1.2", True, cycle, 0.85, {"cycle_position": cycle}, "周期定位"),
        ],
        gray_zone=GrayZoneVerdict(True, ("5.2",), ("关键节点 5.2 置信度 0.60 < 0.80",)),
        llm_called=True,
        llm_answer={"cycle_position": cycle, "signal_bar_quality": "pass"},
        prompt_stats={
            "bytes": 4096,
            "chars": 2048,
            "sha256": "a" * 64,
            "loaded": ["persona"],
        },
    )


def _sample(**overrides):
    sample = {
        "schema_version": "v1",
        "sample_id": "S1",
        "generated_at": "2026-11-10T12:00:00",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "contested_nodes": ["5.2"],
        "decision": {"order_direction": "做多", "entry_price": 100.0,
                     "stop_loss_price": 98.0, "take_profit_price": 104.0},
        "prompt": {"bytes": 2048, "sha256": "b" * 64},
        "llm_called": True,
        "forward_outcome": {"status": "pending", "bars": 10},
    }
    sample.update(overrides)
    return sample


def test_append_deduplicates_by_sample_id() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        append_sample(_sample(), records_dir=root)
        append_sample(_sample(), records_dir=root)
        assert len(load_samples(records_dir=root)) == 1


def test_load_across_months() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        append_sample(_sample(sample_id="S1"), records_dir=root)
        append_sample(
            _sample(sample_id="S2", generated_at="2026-12-01T00:00:00"), records_dir=root
        )
        assert len(load_samples(records_dir=root)) == 2
        assert len(load_samples(records_dir=root, month="2026-11")) == 1


def test_build_sample_redacts_prompt_and_keeps_hash() -> None:
    outcome = _outcome()
    secret_prompt = "PROMPT-BODY-SHOULD-NOT-BE-STORED " * 50
    sample = build_sample(
        outcome, symbol="BTCUSDT", timeframe="1h", prompt=secret_prompt, anchor_ts=123.0
    )
    dumped = json.dumps(sample, ensure_ascii=False)
    assert "PROMPT-BODY-SHOULD-NOT-BE-STORED" not in dumped
    assert sample["prompt"]["sha256"] == "a" * 64
    assert sample["prompt"]["bytes"] == 4096
    assert sample["anchor_ts"] == 123.0
    assert sample["forward_outcome"]["status"] == "pending"
    assert sample["decision"]["entry_price"] == 100.0
    # 证据只保留标量，且只覆盖争议节点
    assert set(sample["code_evidence"]) == {"5.2"}
    assert sample["code_evidence"]["5.2"]["evidence"]["pullback_pct"] == 0.29


def test_build_sample_without_hash_uses_prompt_digest() -> None:
    outcome = _outcome()
    outcome.prompt_stats = {"bytes": 10, "chars": 10}
    sample = build_sample(outcome, symbol="ETHUSDT", timeframe="15m", prompt="abc")
    assert len(sample["prompt"]["sha256"]) == 64
    assert sample["llm_answer"]["signal_bar_quality"] == "pass"


def test_label_forward_tp1() -> None:
    label = label_forward(_sample()["decision"], [_Bar(high=104.5, low=99.5, close=103.0)])
    assert label["status"] == "tp1"
    assert label["bars_used"] == 1
    assert label["r_multiple"] > 0


def test_label_forward_stop_and_both() -> None:
    stop = label_forward(_sample()["decision"], [_Bar(high=101.0, low=97.0, close=97.5)])
    assert stop["status"] == "stop"
    assert stop["r_multiple"] == -1.0

    both = label_forward(_sample()["decision"], [_Bar(high=105.0, low=97.0, close=100.0)])
    assert both["status"] == "both"


def test_label_forward_open_and_no_plan() -> None:
    open_label = label_forward(
        _sample()["decision"], [_Bar(high=101.0, low=99.0, close=101.0)], forward_bars=1
    )
    assert open_label["status"] == "open"
    assert open_label["bars_used"] == 1

    no_plan = label_forward({"order_direction": None}, [_Bar(1, 0, 1)])
    assert no_plan["status"] == "no_plan"


def test_update_forward_outcome_persists() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        append_sample(_sample(), records_dir=root)
        assert update_forward_outcome("S1", {"status": "tp1"}, records_dir=root) is True
        assert update_forward_outcome("missing", {"status": "tp1"}, records_dir=root) is False
        stored = load_samples(records_dir=root)[0]
        assert stored["forward_outcome"]["status"] == "tp1"


def test_summarize_counts_status_and_nodes() -> None:
    samples = [
        _sample(sample_id="S1"),
        _sample(
            sample_id="S2",
            forward_outcome={"status": "tp1"},
            contested_nodes=["5.2", "9.0"],
            symbol="ETHUSDT",
        ),
    ]
    report = summarize(samples)
    assert report["samples"] == 2
    assert report["llm_called"] == 2
    assert report["forward"]["pending"] == 1
    assert report["forward"]["tp1"] == 1
    assert report["contested_top"][0] == ("5.2", 2)
    assert report["by_symbol"] == {"BTCUSDT": 1, "ETHUSDT": 1}
    assert report["prompt_bytes"]["max"] == 2048
    assert summarize([])["samples"] == 0


def test_save_decision_captures_gray_sample() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        outcome = _outcome()
        path = save_decision(
            outcome,
            symbol="BTCUSDT",
            timeframe="1h",
            records_dir=root,
            anchor_ts=1700000000000.0,
            now=datetime(2026, 11, 10, 12, 0, 0),
        )
        assert path.is_file()
        samples = load_samples(records_dir=root)
        assert len(samples) == 1
        assert samples[0]["anchor_ts"] == 1700000000000.0
        assert samples[0]["symbol"] == "BTCUSDT"


def test_save_decision_skips_non_gray() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        outcome = _outcome()
        outcome.llm_called = False
        save_decision(outcome, symbol="BTCUSDT", timeframe="1h", records_dir=root)
        assert load_samples(records_dir=root) == []
