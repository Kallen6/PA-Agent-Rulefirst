"""T2.7 同一快照 v1/v2 对照工具单测。"""
from __future__ import annotations

from unittest import SkipTest

from tests.support import V1_OK


def _require_v1() -> None:
    if not V1_OK:
        raise SkipTest("v1 (PA_Agent) 不可用")


def test_synthetic_regimes_have_no_field_diff() -> None:
    _require_v1()
    from tools.compare_v1_v2 import compare_snapshot
    from tests.fixtures.synthetic import make_regime_rows

    for regime in ("micro_channel", "tight_channel", "trading_range", "spike"):
        result = compare_snapshot(make_regime_rows(regime))
        assert result["matched"] is True, (regime, result["diffs"])


def test_record_snapshot_rebuilds_both_frames() -> None:
    _require_v1()
    from tools.compare_v1_v2 import _rows_from_record, compare_snapshot
    from tests.fixtures.synthetic import make_regime_rows
    from tests.fixtures.v1_records import record_from_frame
    from tests.support import build_v2_frame

    frame = build_v2_frame(make_regime_rows("normal_channel"))
    record = record_from_frame(frame)
    rows = _rows_from_record(record)
    assert rows[0]["ts_open"] < rows[-1]["ts_open"]
    result = compare_snapshot(rows)
    assert result["fields"]["direction"]["ok"] is True
