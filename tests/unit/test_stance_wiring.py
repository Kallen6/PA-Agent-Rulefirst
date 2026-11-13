"""档位接线：decide 透传、记录字段、Web 请求校验、定时脚本 CSV 列。"""
from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.fixtures.synthetic import make_regime_rows, make_setup_rows
from tests.support import build_v2_frame

from pa_engine.engine.pipeline import decide
from pa_engine.engine.stance import apply_stance
from pa_engine.gates.base import GateContext
from pa_engine.records.writer import build_record_payload


def _frame():
    return build_v2_frame(make_setup_rows("tight_channel"))


def test_decide_defaults_to_balanced() -> None:
    outcome = decide(_frame())
    decision = outcome.decision
    assert decision["stance"]["name"] == "balanced"
    assert decision["stance"]["min_rr"] == 1.2
    assert outcome.stage1["stance"] == "balanced"


def test_decide_honours_explicit_stance() -> None:
    conservative = decide(_frame(), stance="conservative").decision
    extreme = decide(_frame(), stance="极度激进").decision
    assert conservative["stance"]["name"] == "conservative"
    assert conservative["stance"]["min_rr"] == 1.5
    assert extreme["stance"]["name"] == "extreme_aggressive"
    assert extreme["stance"]["min_equation_gap"] == -0.05


def test_ctx_preset_stance_is_not_overwritten() -> None:
    ctx = GateContext()
    apply_stance(ctx, "aggressive")
    decision = decide(_frame(), ctx=ctx).decision
    assert decision["stance"]["name"] == "aggressive"


def test_stance_does_not_change_direction_or_cycle() -> None:
    frame = build_v2_frame(make_regime_rows("broad_channel"))
    base = decide(frame, stance="conservative").decision
    loose = decide(frame, stance="extreme_aggressive").decision
    for key in ("cycle_position", "direction", "always_in", "gate_result", "entry_style"):
        assert base[key] == loose[key], key


def test_record_payload_carries_stance_snapshot() -> None:
    outcome = decide(_frame(), stance="conservative")
    payload = build_record_payload(outcome, symbol="TEST", timeframe="1h")
    assert payload["stance"]["name"] == "conservative"
    assert payload["stance"]["min_rr"] == 1.5
    assert payload["decision"]["stance"]["name"] == "conservative"


def test_trade_confidence_recorded_only_when_trading() -> None:
    """P1：交易信心＝生效胜率；不下单时为 null，可直接按档位回测。"""
    frame = build_v2_frame(make_setup_rows("tight_channel"))
    trade = decide(frame, stance="aggressive").decision
    if trade["terminal"]["outcome"] == "trade":
        assert trade["trade_confidence"] is not None
        assert 0 <= trade["trade_confidence"] <= 100
        assert trade["trade_confidence"] == round(trade["estimated_win_rate"])
    wait = decide(build_v2_frame(make_regime_rows("tight_channel")), stance="balanced").decision
    assert wait["trade_confidence"] is None


def test_aggressive_falls_back_to_background_limit() -> None:
    """P0 配套：激进档信号路径被否决后回退 §9.0P（broad_channel 经典路径）。"""
    frame = build_v2_frame(make_regime_rows("broad_channel"))
    decision = decide(frame, stance="aggressive").decision
    assert decision["entry_style"] == "background_limit"
    # 均衡档不回退：路径顺序语义不变（score=2 走 borderline → 9.0P 主路径）
    balanced = decide(frame, stance="balanced").decision
    assert balanced["entry_style"] in ("background_limit", "none")


def test_web_kwargs_validate_stance() -> None:
    from pa_engine.web.server import _decide_kwargs

    assert _decide_kwargs({"stance": "conservative"})["stance"] == "conservative"
    assert _decide_kwargs({"stance": "均衡"})["stance"] == "balanced"
    try:
        _decide_kwargs({"stance": "bogus"})
    except ValueError as exc:
        assert "未知下单倾向档位" in str(exc)
    else:  # pragma: no cover - 只在未校验时触发
        raise AssertionError("非法档位必须返回 400")


def test_scheduled_csv_has_stance_column() -> None:
    from tools.scheduled_decision_log import FIELDNAMES, HEADER_ZH, collect_fields

    assert "decision_stance" in FIELDNAMES
    assert "current_strategy" in FIELDNAMES
    assert HEADER_ZH["decision_stance"] == "下单倾向"
    assert HEADER_ZH["current_strategy"] == "当前策略"
    frame = _frame()
    row = collect_fields(frame, decide(frame, stance="conservative"))
    assert row["decision_stance"] == "保守（RR≥1.5）"
    assert row["current_strategy"] == "保守策略"


def test_append_csv_aligns_legacy_header() -> None:
    """旧 CSV 没有档位列时，按实际表头写行，不得错位。"""
    from tools.scheduled_decision_log import FIELDNAMES, HEADER_ZH, append_csv

    legacy = tuple(key for key in FIELDNAMES if key != "decision_stance")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "log.csv"
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow([HEADER_ZH[key] for key in legacy])
            writer.writerow(["old"] * len(legacy))

        append_csv(path, {key: key for key in FIELDNAMES})

        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.reader(handle))
        assert rows[0] == [HEADER_ZH[key] for key in legacy]
        assert len(rows[2]) == len(legacy)
        assert rows[2][rows[0].index(HEADER_ZH["timeframe"])] == "timeframe"
        assert rows[2][rows[0].index(HEADER_ZH["symbol"])] == "symbol"
