"""下单倾向档位的参数表与归一化（纯函数，无 IO / 无 LLM）。"""
from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pa_engine.config.thresholds import Stance
from pa_engine.engine.stance import (
    StancePolicy,
    all_stances,
    is_valid_stance,
    normalize_stance,
    policy_of,
)


def test_default_is_balanced() -> None:
    assert Stance.DEFAULT == "balanced"
    assert policy_of("").name == "balanced"
    assert policy_of(None).name == "balanced"  # type: ignore[arg-type]


def test_four_tiers_are_complete_and_ordered() -> None:
    assert Stance.ORDER == (
        "conservative",
        "balanced",
        "aggressive",
        "extreme_aggressive",
    )
    assert set(Stance.PARAMS) == set(Stance.ORDER)
    assert set(Stance.LABELS_ZH) == set(Stance.ORDER)
    assert [item["name"] for item in all_stances()] == list(Stance.ORDER)


def test_parameters_are_monotonic() -> None:
    """档位从保守到激进，各门槛必须单调放宽（或维持不变）。"""
    values = [policy_of(name) for name in Stance.ORDER]
    min_rr = [item.min_rr for item in values]
    gaps = [item.min_equation_gap for item in values]
    wins = [item.min_win_rate for item in values]
    passes = [item.signal_pass_score for item in values]
    fails = [item.signal_fail_score for item in values]
    bg_min = [item.bg_limit_min_mult for item in values]
    bg_max = [item.bg_limit_max_mult for item in values]
    fallbacks = [item.fallback_to_bg_limit for item in values]
    assert min_rr == sorted(min_rr, reverse=True), min_rr
    assert gaps == sorted(gaps, reverse=True), gaps
    assert wins == sorted(wins, reverse=True), wins
    assert passes == sorted(passes, reverse=True), passes
    assert fails == sorted(fails, reverse=True), fails
    assert bg_min == sorted(bg_min, reverse=True), bg_min  # 距离下限：保守更远、激进更近
    assert bg_max == sorted(bg_max), bg_max  # 距离上限：保守更近、激进更远
    assert fallbacks == [False, False, True, True], fallbacks
    # target_rr 必须略高于 min_rr，否则外扩后会被自己的门槛判死
    for item in values:
        assert item.target_rr > item.min_rr, item
    # 保守档：borderline(2) 直接判不合格；均衡档保持原始三分诊口径
    assert policy_of("conservative").signal_pass_score == 4
    assert policy_of("conservative").signal_fail_score == 2
    assert policy_of("balanced").signal_pass_score == 3
    assert policy_of("balanced").signal_fail_score == 1


def test_aggressive_lifts_borderline_and_falls_back() -> None:
    """P0 核心：激进档把 §9.0 的 2 分边缘信号判为合格，并启用 9.0P 回退。"""
    aggressive = policy_of("aggressive")
    assert aggressive.signal_pass_score == 2  # borderline → pass
    assert aggressive.fallback_to_bg_limit is True
    extreme = policy_of("extreme_aggressive")
    assert extreme.signal_fail_score == 0  # 1 分从「不合格」降级为灰区
    balanced = policy_of("balanced")
    assert balanced.fallback_to_bg_limit is False  # 路径顺序语义不变


def test_normalize_accepts_chinese_and_alias() -> None:
    assert normalize_stance("保守") == "conservative"
    assert normalize_stance("均衡") == "balanced"
    assert normalize_stance("激进") == "aggressive"
    assert normalize_stance("极度激进") == "extreme_aggressive"
    assert normalize_stance("extreme") == "extreme_aggressive"
    assert normalize_stance(" EXTREME_AGGRESSIVE ") == "extreme_aggressive"


def test_unknown_stance_falls_back_to_default() -> None:
    assert normalize_stance("bogus") == Stance.DEFAULT
    assert normalize_stance("") == Stance.DEFAULT
    assert policy_of("bogus").name == Stance.DEFAULT
    assert is_valid_stance("bogus") is False
    assert is_valid_stance("均衡") is True


def test_policy_is_immutable_and_recordable() -> None:
    policy = policy_of("conservative")
    assert isinstance(policy, StancePolicy)
    try:
        policy.min_rr = 0.1  # type: ignore[misc]
    except FrozenInstanceError:
        pass
    else:  # pragma: no cover - 只在不冻结时触发
        raise AssertionError("StancePolicy 必须是不可变对象")
    record = policy.as_record()
    assert record["name"] == "conservative"
    assert record["label_zh"] == "保守"
    assert record["min_rr"] == 1.5
    assert record["min_equation_gap"] == 0.15
    assert record["target_rr"] == 1.55
    assert record["min_win_rate"] == 55.0
    assert record["signal_pass_score"] == 4
    assert record["fallback_to_bg_limit"] is False


def test_policy_lookup_is_cached() -> None:
    assert policy_of("aggressive") is policy_of("aggressive")
