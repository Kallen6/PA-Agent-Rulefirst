"""特征层与 v1 的 parity 测试（只读 import v1）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.support import V1_OK, build_dual_frames, make_raw_rows, nan_equal

if V1_OK:
    from pa_agent.ai.kline_features import compute_kline_geometry_features as v1_kline
    from pa_agent.ai.market_features import compute_simple_market_features as v1_struct
    from pa_engine.features.kline import compute_kline_geometry_features as v2_kline
    from pa_engine.features.structure import compute_simple_market_features as v2_struct


def _skip_if_unavailable() -> bool:
    return not V1_OK


def test_kline_geometry_field_parity() -> None:
    if _skip_if_unavailable():
        print("  [skip] v1 不可用")
        return
    frame_v1, frame_v2 = build_dual_frames(make_raw_rows())
    f1 = v1_kline(frame_v1)
    f2 = v2_kline(frame_v2)
    assert len(f1) == len(f2)
    for a, b in zip(f1, f2):
        for key, value in a.__dict__.items():
            assert nan_equal(value, getattr(b, key)), f"seq={a.seq} field={key}"


def test_structure_field_parity() -> None:
    if _skip_if_unavailable():
        print("  [skip] v1 不可用")
        return
    frame_v1, frame_v2 = build_dual_frames(make_raw_rows())
    s1 = v1_struct(frame_v1)
    s2 = v2_struct(frame_v2)
    skip = {"swings", "breakout_events", "measured_moves", "hl_count"}
    for key, value in s1.__dict__.items():
        if key in skip:
            continue
        assert nan_equal(value, getattr(s2, key)), f"structure field={key}"
    for key, value in s1.hl_count.__dict__.items():
        assert value == getattr(s2.hl_count, key), f"hl_count field={key}"
    assert len(s1.swings) == len(s2.swings)
    assert len(s1.breakout_events) == len(s2.breakout_events)
    assert len(s1.measured_moves) == len(s2.measured_moves)
