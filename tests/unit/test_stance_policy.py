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
    """档位从保守到激进，RR 门槛与方程余量必须单调放宽。"""
    values = [policy_of(name) for name in Stance.ORDER]
    min_rr = [item.min_rr for item in values]
    gaps = [item.min_equation_gap for item in values]
    assert min_rr == sorted(min_rr, reverse=True), min_rr
    assert gaps == sorted(gaps, reverse=True), gaps
    # target_rr 必须略高于 min_rr，否则外扩后会被自己的门槛判死
    for item in values:
        assert item.target_rr > item.min_rr, item


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


def test_policy_lookup_is_cached() -> None:
    assert policy_of("aggressive") is policy_of("aggressive")
