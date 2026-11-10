"""审计工具单测：分歧归因规则、报告区块合并、走势定位、季度复核。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from tools.attribute_divergence import attribute_row, build_report
from tools.label_gray_samples import _later_bars
from tools.quarterly_review import review

from pa_engine.data.types import KlineBar
from pa_engine.records.audit import upsert_section
from tests.fixtures.synthetic import make_regime_rows
from tests.support import build_v2_frame


def _row(**overrides):
    row = {
        "record": "r1",
        "actual": {"cycle_position": "tight_channel", "terminal_node": "11.4"},
        "expected": {
            "cycle_position": "tight_channel",
            "gate_result": "proceed",
            "direction": "bullish",
            "always_in": "bullish",
            "order_type": "限价单",
            "terminal_outcome": "trade",
        },
        "diffs": {"cycle_position": {"ok": False}},
        "min_margin": 0.01,
        "min_margin_field": "5.2:pullback_pct",
    }
    row.update(overrides)
    return row


def test_attribute_matched_row() -> None:
    item = attribute_row(_row(diffs={}))
    assert item["category"] == "matched"


def test_attribute_v1_inconsistent() -> None:
    expected = dict(_row()["expected"])
    expected["order_type"] = "限价单"
    expected["terminal_outcome"] = "wait"
    item = attribute_row(_row(expected=expected))
    assert item["category"] == "v1_inconsistent"
    assert "有下单" in item["reason"]


def test_attribute_v1_unknown_cycle_proceed() -> None:
    expected = dict(_row()["expected"])
    expected["cycle_position"] = "unknown"
    expected["gate_result"] = "proceed"
    item = attribute_row(_row(expected=expected))
    assert item["category"] == "v1_inconsistent"


def test_attribute_v2_gap() -> None:
    actual = {"cycle_position": "unknown", "terminal_node": "1.2"}
    item = attribute_row(_row(actual=actual))
    assert item["category"] == "v2_gap"
    assert "周期" in item["reason"]


def test_attribute_threshold_uses_band() -> None:
    item = attribute_row(_row(min_margin=0.02), band=0.05)
    assert item["category"] == "threshold"
    assert "余量" in item["reason"]

    far = attribute_row(_row(min_margin=0.30), band=0.05)
    assert far["category"] == "other"


def test_build_report_without_inputs() -> None:
    report = build_report(replay=None, compare=None, samples=[])
    assert report["status"] == "N/A"
    assert report["replay"]["samples"] == 0
    assert report["degraded"]


def test_build_report_attributes_replay_rows() -> None:
    replay = {"status": "ok", "samples": 1, "rows": [_row()]}
    report = build_report(replay=replay, compare=None, samples=[])
    assert report["status"] == "ok"
    assert report["replay"]["diff_rows"] == 1
    assert report["replay"]["attribution"]["threshold"] == 1


def test_upsert_section_keeps_other_blocks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "10.md"
        upsert_section(path, "divergence", "A")
        upsert_section(path, "quarterly", "B")
        upsert_section(path, "divergence", "A2")
        text = path.read_text(encoding="utf-8")
        assert "A2" in text and "B" in text and "A" not in text.replace("A2", "")
        assert text.count("<!-- audit:divergence:start -->") == 1


def test_later_bars_filters_and_sorts() -> None:
    bars = [
        KlineBar(seq=3, ts_open=300.0, open=1, high=1, low=1, close=1, volume=0),
        KlineBar(seq=2, ts_open=200.0, open=1, high=1, low=1, close=1, volume=0),
        KlineBar(seq=1, ts_open=100.0, open=1, high=1, low=1, close=1, volume=0),
    ]
    later = _later_bars(bars, 150.0)
    assert [bar.ts_open for bar in later] == [200.0, 300.0]


def test_quarterly_review_is_read_only_and_reports_drift() -> None:
    frames = [
        build_v2_frame(make_regime_rows("tight_channel", n=80)),
        build_v2_frame(make_regime_rows("broad_channel", n=80)),
    ]
    report = review(frames, current_band=0.05)
    assert report["samples"] == 2
    assert report["current_band"] == 0.05
    assert report["recommended_band"] in {0.05, 0.08, 0.10, 0.12, 0.15, 0.20}
    assert report["status"] in ("stable", "drift")
    assert isinstance(report["alerts"], list)
    assert len(report["rows"]) >= 6
