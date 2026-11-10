"""T1.6 pa_validation 等价回归单测。"""
from __future__ import annotations

from unittest import SkipTest

from tests.support import V1_OK


def _require_v1() -> None:
    if not V1_OK:
        raise SkipTest("v1 (PA_Agent) 不可用")


def test_feature_parity_and_micro_detection_are_stable() -> None:
    _require_v1()
    from tools.pa_validation_regression import _build_frames, _run_frame
    from tests.fixtures.synthetic import make_regime_rows

    result = _run_frame(*_build_frames(make_regime_rows("tight_channel")))
    assert result["features_parity"]["rate"] == 1.0
    assert result["features_parity"]["mismatches"] == 0
    assert result["micro"]["v1"] == result["micro"]["v2"]
    assert result["pullback_parity"] is True


def test_follow_through_and_buckets_are_computed() -> None:
    _require_v1()
    from tools.pa_validation_regression import _build_frames, _run_frame
    from tests.fixtures.synthetic import make_regime_rows

    result = _run_frame(*_build_frames(make_regime_rows("normal_channel")))
    assert set(result["follow"]["v2"]) == {"strong", "weak"}
    assert set(result["pullback_depth"]["bucket"]) == {"tight", "normal", "broad", "deep"}
    assert result["follow"]["v2"]["strong"]["total"] >= 0
