"""T0.6/T1.5 回放工具单测：解析、字段比对、容差、N/A、坏记录。"""
from __future__ import annotations

from tools.replay_v1_records import compare_record, replay_records
from tests.fixtures.synthetic import make_regime_rows, make_setup_rows
from tests.fixtures.v1_records import record_from_frame
from tests.support import build_v2_frame


def _record(regime: str, setup: bool = False):
    rows = make_setup_rows(regime) if setup else make_regime_rows(regime)
    frame = build_v2_frame(rows)
    return frame, record_from_frame(frame)


def test_fixture_record_replays_without_diff() -> None:
    frame, record = _record("micro_channel")
    result = compare_record(record)
    assert result["matched"] is True
    assert result["diffs"] == {}


def test_field_stats_and_overall_rate() -> None:
    records = [_record("micro_channel")[1], _record("tight_channel", setup=True)[1]]
    report = replay_records(records)
    assert report["samples"] == 2
    assert report["overall_rate"] == 1.0
    assert report["field_stats"]["cycle_position"]["matched"] == 2
    assert report["status"] == "ok"


def test_empty_records_reports_na() -> None:
    report = replay_records([])
    assert report["status"] == "N/A"
    assert report["samples"] == 0
    assert report["overall_rate"] is None


def test_price_tolerance_accepts_small_rounding() -> None:
    frame, record = _record("micro_channel")
    decision = record["stage2_decision"]["decision"]
    if decision.get("entry_price") is None:
        return
    decision["entry_price"] = float(decision["entry_price"]) * 1.001
    result = compare_record(record)
    assert "entry_price" not in result["diffs"]


def test_broken_record_is_collected_not_raised() -> None:
    _, good = _record("micro_channel")
    bad = {"meta": {"timestamp_local_iso": "bad"}, "kline_data": []}
    report = replay_records([good, bad])
    assert report["samples"] == 1
    assert len(report["errors"]) == 1


def test_v1_direction_aliases_are_normalized() -> None:
    from pa_engine.adapters.v1_records import stage1_fields

    fields = stage1_fields(
        {
            "stage1_diagnosis": {
                "cycle_position": "micro_channel",
                "direction": "bullish",
                "bar_analysis": {"always_in": "long"},
                "gate_result": "proceed",
            }
        }
    )
    assert fields["direction"] == "bullish"
    assert fields["always_in"] == "bullish"
