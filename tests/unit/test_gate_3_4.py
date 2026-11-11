"""§3.4 首次暂停/回撤（SPS 前提）闸门单测。"""
from __future__ import annotations

from pa_engine.config.thresholds import Spike
from pa_engine.gates.base import GateContext
from pa_engine.gates.spike import gate_3_4
from tests.support import build_v2_frame


def _rows(n_flat: int, n_run: int, n_pause: int) -> list[dict]:
    """平盘 → 强单边 run → 小实体回撤（回撤棒实体 <0.35×ATR，不构成反向 run）。"""
    rows = []
    price = 100.0
    ts = 1_000_000
    for i in range(n_flat):
        rows.append(
            {
                "ts_open": ts + i * 60_000,
                "open": price,
                "high": price + 0.4,
                "low": price - 0.4,
                "close": price,
                "volume": 1000.0,
            }
        )
    for j in range(n_run):
        open_ = price
        close = open_ + 3.0
        rows.append(
            {
                "ts_open": ts + (n_flat + j) * 60_000,
                "open": open_,
                "high": close + 0.1,
                "low": open_ - 0.1,
                "close": close,
                "volume": 1000.0,
            }
        )
        price = close
    for k in range(n_pause):
        open_ = price
        close = open_ - 0.25
        rows.append(
            {
                "ts_open": ts + (n_flat + n_run + k) * 60_000,
                "open": open_,
                "high": open_ + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 1000.0,
            }
        )
        price = close
    return rows


def _decide_gate(rows: list[dict]) -> object:
    frame = build_v2_frame(rows)
    return gate_3_4(frame, GateContext(extras={"direction": "bullish"}))


def test_first_pause_within_window() -> None:
    result = _decide_gate(_rows(40, 4, 3))
    assert result.answer is True
    assert result.branch == "pause"
    assert result.evidence["pause_bars"] == 3


def test_spike_ongoing_no_pause() -> None:
    result = _decide_gate(_rows(40, 4, 0))
    assert result.answer is False
    assert result.branch == "ongoing"


def test_long_pause_outside_window() -> None:
    result = _decide_gate(_rows(40, 4, 6))
    assert result.answer is False
    assert result.branch == "long_pause"
    assert result.evidence["pause_bars"] == 6


def test_no_spike_not_applicable() -> None:
    from tests.fixtures.synthetic import make_regime_rows

    frame = build_v2_frame(make_regime_rows("trading_range"))
    result = gate_3_4(frame, GateContext())
    assert result.answer == "n/a"


def test_thresholds_window() -> None:
    assert Spike.FIRST_PAUSE_MIN_BARS == 2
    assert Spike.FIRST_PAUSE_MAX_BARS == 4


def test_gate_registered_in_stage1() -> None:
    from pa_engine.engine.pipeline import STAGE1_GATES

    assert "3.4" in STAGE1_GATES
    # 顺序：3.4 在 3.3 与 3.5 之间（v1 执行顺序）
    assert STAGE1_GATES.index("3.3") < STAGE1_GATES.index("3.4") < STAGE1_GATES.index("3.5")
