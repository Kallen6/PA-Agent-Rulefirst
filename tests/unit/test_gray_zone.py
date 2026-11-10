"""灰区检测器测试。"""
from __future__ import annotations

from pa_engine.config.thresholds import GrayZone as GZ
from pa_engine.engine.gray_zone import Measurement, contested_measurements, evaluate
from pa_engine.gates.base import GateContext, run_all


class _Frame:
    bars: tuple = ()
    indicators = None


def test_measurement_near_threshold_is_contested() -> None:
    ms = [
        Measurement("pullback_pct", 0.29, 0.30),               # 余量 3.3% ≤ BAND 5% → 灰区
        Measurement("overlap", 0.10, 0.30),                    # 余量 66% → 不灰区
    ]
    names = [name for name, _ in contested_measurements(ms)]
    assert "pullback_pct" in names
    assert "overlap" not in names


def test_missing_critical_nodes_require_llm() -> None:
    results = run_all(_Frame(), GateContext())  # 只有 §3.3 / §9.1
    verdict = evaluate(results)
    assert verdict.need_llm is True
    assert "2.4" in verdict.contested_nodes
    assert any("缺失" in r for r in verdict.reasons)


def test_band_widening_increases_contested_count() -> None:
    ms = [Measurement("pullback_pct", 0.22, 0.30)]  # 余量 26.7%
    assert contested_measurements(ms, band=0.10) == []
    assert contested_measurements(ms, band=0.30) != []
