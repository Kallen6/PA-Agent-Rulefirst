"""锚定块单测：内容、预算、降级优先序、契约可选字段。"""
from __future__ import annotations

from pa_engine.ai.contract import parse_payload
from pa_engine.ai.prompt import anchor_block, build_gray_zone_prompt
from pa_engine.config.thresholds import GrayZone as GZ
from pa_engine.engine.pipeline import decide
from tests.fixtures.synthetic import make_regime_rows
from tests.support import build_v2_frame

ANCHOR = {
    "generated_at": "2026-11-10T10:00:00",
    "new_bars": 12,
    "previous": {
        "cycle_position": "tight_channel",
        "direction": "bullish",
        "always_in": "bullish",
        "gate_result": "proceed",
        "order_type": "限价单",
        "terminal": {"node_id": "11.4", "outcome": "trade"},
    },
}


def _context():
    frame = build_v2_frame(make_regime_rows("broad_channel"))
    outcome = decide(frame)
    assert outcome.gray_zone is not None
    return frame, outcome.gate_results, outcome.gray_zone


def test_anchor_block_renders_key_facts() -> None:
    text = anchor_block(ANCHOR)
    assert "上次结论" in text
    assert "新增已收盘K线：12 根" in text
    assert "tight_channel" in text
    assert "11.4" in text


def test_prompt_contains_anchor_when_provided() -> None:
    frame, results, gray = _context()
    text = build_gray_zone_prompt(frame, results, gray, anchor=ANCHOR)
    assert "## 上次结论" in text
    assert len(text.encode("utf-8")) <= GZ.MAX_PROMPT_BYTES


def test_anchor_is_trimmed_first_when_over_budget() -> None:
    frame, results, gray = _context()
    full = build_gray_zone_prompt(frame, results, gray, anchor=ANCHOR)
    original = GZ.MAX_PROMPT_BYTES
    try:
        GZ.MAX_PROMPT_BYTES = len(full.encode("utf-8")) - 1
        trimmed = build_gray_zone_prompt(frame, results, gray, anchor=ANCHOR)
    finally:
        GZ.MAX_PROMPT_BYTES = original

    assert len(trimmed.encode("utf-8")) <= original
    assert "## 上次结论" not in trimmed
    # 人设 / 争议节点 / 输出契约不裁剪
    assert "## 输出契约" in trimmed
    assert "## 待复核问题" in trimmed


def test_prompt_without_anchor_has_no_anchor_block() -> None:
    frame, results, gray = _context()
    text = build_gray_zone_prompt(frame, results, gray)
    assert "## 上次结论" not in text


def test_contract_optional_fields_are_validated() -> None:
    answer = parse_payload(
        {
            "cycle_position": "tight_channel",
            "direction": "bullish",
            "always_in": "bullish",
            "signal_bar_quality": "pass",
            "estimated_win_rate": 0.62,
            "wedge_type": "parabolic",
            "reversal_attempt": "true",
            "second_entry": "second",
            "entry_quality": "strong",
        }
    )
    assert answer is not None
    assert answer.estimated_win_rate == 62.0
    assert answer.wedge_type == "parabolic"
    assert answer.reversal_attempt is True
    assert answer.second_entry == "second"
    assert answer.entry_quality == "strong"


def test_contract_rejects_unknown_enum_values() -> None:
    answer = parse_payload(
        {
            "cycle_position": "not_a_state",
            "wedge_type": "flag",
            "second_entry": "ninth",
            "entry_quality": "excellent",
            "estimated_win_rate": 250,
        }
    )
    assert answer is not None
    assert answer.cycle_position == "unknown"
    assert answer.wedge_type == "none"
    assert answer.second_entry == "unknown"
    assert answer.entry_quality == "unknown"
    assert answer.estimated_win_rate == 100.0
