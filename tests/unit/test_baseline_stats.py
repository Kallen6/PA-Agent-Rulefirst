"""基线测量与带宽标定的统计逻辑单测（不联网）。"""
from __future__ import annotations

from pa_engine.engine.pipeline import decide
from tests.fixtures.synthetic import REGIMES, make_regime_rows
from tests.support import build_v2_frame
from tools.baseline_measure import _aggregate, _percentile
from tools.calibrate_band import _signature, scan


def test_percentile_nearest_rank() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _percentile(values, 0.5) == 3.0
    assert _percentile(values, 0.95) == 5.0
    assert _percentile([], 0.5) is None


def test_aggregate_sums_decisions_and_ratios() -> None:
    series = [
        {
            "decisions": 10,
            "gray_ratio": 0.2,
            "cycles": {"a": 10},
            "outcomes": {"trade": 2},
            "entry_styles": {"signal": 2},
            "gray_reasons": [("r1", 2)],
            "latency_ms": {"p50": 4.0},
            "prompt_bytes": {"avg": 9000.0},
        },
        {
            "decisions": 10,
            "gray_ratio": 0.4,
            "cycles": {"b": 10},
            "outcomes": {"wait": 8},
            "entry_styles": {"none": 8},
            "gray_reasons": [("r1", 1)],
            "latency_ms": {"p50": 6.0},
            "prompt_bytes": {"avg": 9500.0},
        },
    ]
    agg = _aggregate(series)
    assert agg["decisions"] == 20
    assert agg["gray_ratio"] == 0.3
    assert agg["latency_p50_avg"] == 5.0
    assert agg["prompt_bytes_avg"] == 9250.0


def test_decision_signature_tracks_prices_and_terminal() -> None:
    decision = decide(build_v2_frame(make_regime_rows("micro_channel"))).decision
    sig = _signature(decision)
    assert sig[0] == decision["cycle_position"]
    assert sig[3] == decision["order_type"]
    assert sig[-2] == decision["terminal"]["node_id"]


def test_band_scan_selects_a_working_point() -> None:
    frames = [build_v2_frame(make_regime_rows(regime)) for regime in REGIMES]
    report = scan(frames, candidates=(0.05, 0.10, 0.20))
    assert report["samples"] == len(frames)
    assert report["selected"]["band"] in (0.05, 0.10, 0.20)
    assert report["selected"]["gray_ratio"] is not None
    assert len(report["rows"]) == 3
