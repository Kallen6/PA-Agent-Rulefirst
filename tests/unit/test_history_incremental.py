"""增量锚定单测：最新记录检索、新增K线计数、缓存失效、决策不变性。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pa_engine.ai.client import MockLLM
from pa_engine.data.types import KlineBar, build_frame
from pa_engine.engine.pipeline import decide
from pa_engine.records.history import (
    AnchorContext,
    anchor_ts_of,
    build_anchor,
    count_new_bars,
    find_latest_record,
    invalidate_cache,
    timeframe_ms,
)
from tests.fixtures.synthetic import make_regime_rows
from tests.support import build_v2_frame


def _payload(
    *,
    generated_at: str,
    anchor_ts: float,
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    cycle: str = "tight_channel",
    schema: str = "v2",
) -> dict:
    return {
        "schema_version": schema,
        "symbol": symbol,
        "timeframe": timeframe,
        "generated_at": generated_at,
        "snapshot": {"anchor_ts": anchor_ts},
        "stage1": {"cycle_position": cycle},
        "decision": {
            "cycle_position": cycle,
            "direction": "bullish",
            "always_in": "bullish",
            "gate_result": "proceed",
            "entry_style": "signal",
            "order_type": "限价单",
            "terminal": {"node_id": "11.4", "outcome": "trade"},
        },
    }


def _write(root: Path, name: str, payload: dict) -> Path:
    day = root / "2026-11-10"
    day.mkdir(parents=True, exist_ok=True)
    path = day / f"{name}_BTCUSDT_1h.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    invalidate_cache()
    return path


def test_timeframe_ms_parsing() -> None:
    assert timeframe_ms("15m") == 900_000
    assert timeframe_ms("1h") == 3_600_000
    assert timeframe_ms("4h") == 14_400_000
    assert timeframe_ms("1d") == 86_400_000
    assert timeframe_ms("") is None
    assert timeframe_ms("1M") is None


def test_count_new_bars_exact_when_frame_covers_anchor() -> None:
    bars = [
        KlineBar(seq=5 - i, ts_open=float(1000 + (4 - i) * 60), open=1, high=1, low=1, close=1, volume=0)
        for i in range(5)
    ]
    frame = build_frame("TEST", "1h", bars)
    # 帧最新 1000+240，锚点 1000+120 → 之后有 2 根
    assert count_new_bars(frame, 1000 + 120, "1h") == 2
    assert count_new_bars(frame, 1000 + 240, "1h") == 0


def test_count_new_bars_estimates_when_anchor_before_window() -> None:
    bars = [
        KlineBar(seq=3 - i, ts_open=float(3_600_000 * (3 - i)), open=1, high=1, low=1, close=1, volume=0)
        for i in range(3)
    ]
    frame = build_frame("TEST", "1h", bars)
    # 帧覆盖 [1h, 3h]，锚点 -4h → 共 7 根（含帧外的 4 根）
    assert count_new_bars(frame, -4 * 3_600_000, "1h") == 7


def test_anchor_ts_prefers_explicit_snapshot() -> None:
    payload = {
        "snapshot": {"anchor_ts": 123.0, "bars": [{"ts_open": 999.0}]},
    }
    assert anchor_ts_of(payload) == 123.0
    assert anchor_ts_of({"snapshot": {"bars": [{"ts_open": 5.0}, {"ts_open": 9.0}]}}) == 9.0
    assert anchor_ts_of({}) is None


def test_find_latest_record_prefers_newest_generated_at() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root, "10-00-00", _payload(generated_at="2026-11-09T10:00:00", anchor_ts=1.0))
        _write(root, "11-00-00", _payload(generated_at="2026-11-10T11:00:00", anchor_ts=2.0))
        found = find_latest_record(symbol="BTCUSDT", timeframe="1h", records_dir=root)
        assert found is not None
        assert found["generated_at"] == "2026-11-10T11:00:00"


def test_find_latest_record_skips_invalid_and_mismatched() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root, "09-00-00", _payload(generated_at="2026-11-01T09:00:00", anchor_ts=1.0))
        _write(
            root,
            "10-00-00",
            _payload(generated_at="2026-11-09T10:00:00", anchor_ts=2.0, schema="v1"),
        )
        _write(
            root,
            "11-00-00",
            _payload(generated_at="2026-11-10T11:00:00", anchor_ts=3.0, symbol="ETHUSDT"),
        )
        found = find_latest_record(symbol="BTCUSDT", timeframe="1h", records_dir=root)
        assert found is not None
        assert found["generated_at"] == "2026-11-01T09:00:00"


def test_build_anchor_requires_new_bars() -> None:
    frame = build_v2_frame(make_regime_rows("tight_channel"))
    newest = float(frame.bars[0].ts_open)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root, "10-00-00", _payload(generated_at="2026-11-10T10:00:00", anchor_ts=newest))
        assert build_anchor(frame, symbol="BTCUSDT", timeframe="1h", records_dir=root) is None

        _write(
            root,
            "11-00-00",
            _payload(generated_at="2026-11-10T11:00:00", anchor_ts=newest - 3 * 60_000),
        )
        anchor = build_anchor(frame, symbol="BTCUSDT", timeframe="1h", records_dir=root)
        assert anchor is not None
        assert anchor.new_bars == 3
        assert anchor.previous["cycle_position"] == "tight_channel"
        assert anchor.as_record()["new_bars"] == 3
        assert anchor.as_prompt()["previous"]["order_type"] == "限价单"


def test_build_anchor_without_records_returns_none() -> None:
    frame = build_v2_frame(make_regime_rows("tight_channel"))
    with tempfile.TemporaryDirectory() as tmp:
        assert build_anchor(frame, symbol="BTCUSDT", timeframe="1h", records_dir=tmp) is None


def test_decide_anchor_does_not_change_code_decision() -> None:
    frame = build_v2_frame(make_regime_rows("broad_channel"))
    baseline = decide(frame).decision
    anchor = AnchorContext(
        path="x.json",
        generated_at="2026-11-10T10:00:00",
        symbol="TEST",
        timeframe="1h",
        anchor_ts=float(frame.bars[0].ts_open) - 60_000,
        new_bars=1,
        previous={
            "cycle_position": "spike",
            "direction": "bearish",
            "always_in": "bearish",
            "gate_result": "wait",
            "entry_style": "none",
            "order_type": "不下单",
            "terminal": {"node_id": "1.3", "outcome": "wait"},
        },
    )
    llm = MockLLM()
    outcome = decide(frame, llm=llm, anchor=anchor)
    decision = outcome.decision
    for key in ("cycle_position", "direction", "always_in", "gate_result", "order_type",
                "entry_price", "stop_loss_price", "take_profit_price"):
        assert decision[key] == baseline[key], key
    assert decision["incremental"]["new_bars"] == 1
    assert decision["incremental"]["previous"]["cycle_position"] == "spike"
    assert llm.call_count == 1
    assert "anchor" in outcome.prompt_stats["loaded"]


def test_clear_market_with_anchor_makes_zero_calls() -> None:
    frame = build_v2_frame(make_regime_rows("micro_channel"))
    anchor = AnchorContext(
        path="x.json",
        generated_at="2026-11-10T10:00:00",
        symbol="TEST",
        timeframe="1h",
        anchor_ts=float(frame.bars[0].ts_open) - 60_000,
        new_bars=1,
        previous={"cycle_position": "micro_channel", "terminal": {}},
    )
    llm = MockLLM()
    outcome = decide(frame, llm=llm, anchor=anchor)
    assert outcome.gray_zone.need_llm is False
    assert llm.call_count == 0
    assert outcome.decision["incremental"]["new_bars"] == 1
