"""结构翻转冷却单测（v1 two_stage 校验器行为；默认 0 禁用）。"""
from __future__ import annotations

from pa_engine.config.thresholds import Guard
from pa_engine.engine.pipeline import decide
from tests.fixtures.synthetic import make_regime_rows
from tests.support import build_v2_frame


def _anchor(direction: str, new_bars: int) -> dict:
    return {"previous": {"direction": direction}, "new_bars": new_bars}


def test_cooldown_disabled_by_default() -> None:
    # 默认 STRUCTURE_FLIP_COOLDOWN_BARS=0：方向相异也不拦截
    assert Guard.STRUCTURE_FLIP_COOLDOWN_BARS == 0
    decision = decide(
        build_v2_frame(make_regime_rows("broad_channel")),
        anchor=_anchor("bearish", 1),
        stance="aggressive",
    ).decision
    assert decision["terminal"]["outcome"] == "trade"


def test_cooldown_blocks_flip_within_window() -> None:
    original = Guard.STRUCTURE_FLIP_COOLDOWN_BARS
    Guard.STRUCTURE_FLIP_COOLDOWN_BARS = 5
    try:
        decision = decide(
            build_v2_frame(make_regime_rows("broad_channel")),
            anchor=_anchor("bearish", 1),
        ).decision
        assert decision["order_type"] == "不下单"
        assert decision["terminal"]["node_id"] == "14"
        # §14 真实触犯 → reject（v1 decision_tree.py:617-620：§14 语义优先于 wait）
        assert decision["terminal"]["outcome"] == "reject"
        assert "structure_flip_cooldown" in str(decision["terminal"]["label"])
    finally:
        Guard.STRUCTURE_FLIP_COOLDOWN_BARS = original


def test_cooldown_allows_same_direction() -> None:
    original = Guard.STRUCTURE_FLIP_COOLDOWN_BARS
    Guard.STRUCTURE_FLIP_COOLDOWN_BARS = 5
    try:
        decision = decide(
            build_v2_frame(make_regime_rows("broad_channel")),
            anchor=_anchor("bullish", 1),
            stance="aggressive",
        ).decision
        assert decision["terminal"]["outcome"] == "trade"
    finally:
        Guard.STRUCTURE_FLIP_COOLDOWN_BARS = original


def test_cooldown_expires_after_window() -> None:
    original = Guard.STRUCTURE_FLIP_COOLDOWN_BARS
    Guard.STRUCTURE_FLIP_COOLDOWN_BARS = 5
    try:
        decision = decide(
            build_v2_frame(make_regime_rows("broad_channel")),
            anchor=_anchor("bearish", 6),
            stance="aggressive",
        ).decision
        assert decision["terminal"]["outcome"] == "trade"
    finally:
        Guard.STRUCTURE_FLIP_COOLDOWN_BARS = original
