"""Prompt 瘦身与灰区预算测试。

硬约束：
- 灰区单次输入 ≤10KB（UTF-8）；
- 五份核心说明合计 ≤12KB；
- 8 张策略卡每张 ≤1.2KB；
- prompt 必须包含人设、争议节点、输出契约，且不包含与代码重复的完整阈值表。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.fixtures.synthetic import REGIMES, make_regime_rows, make_setup_rows
from tests.support import build_v2_frame

from pa_engine.ai.prompt import PROMPT_DIR, build_gray_zone_prompt, prompt_size, strategy_card
from pa_engine.config.thresholds import GrayZone as GZ
from pa_engine.engine.pipeline import decide

CORE_DOCS = (
    "00_persona.txt",
    "10_stage1_diagnosis.md",
    "20_stage2_execution.md",
    "30_c_class_briefs.md",
    "40_output_contract.md",
)
CARD_BUDGET_BYTES = 1200


def _prompt_for(kind: str, *, setup: bool = False) -> str:
    rows = make_setup_rows(kind) if setup else make_regime_rows(kind)
    frame = build_v2_frame(rows)
    outcome = decide(frame)
    return build_gray_zone_prompt(frame, outcome.gate_results, outcome.gray_zone)


def test_core_docs_within_budget() -> None:
    total = sum((PROMPT_DIR / name).stat().st_size for name in CORE_DOCS)
    assert total <= 12 * 1024, f"核心说明合计 {total} 字节，超过 12KB"


def test_strategy_cards_within_budget() -> None:
    cards = sorted((PROMPT_DIR / "strategies").glob("*.md"))
    assert len(cards) == 8, [c.name for c in cards]
    for card in cards:
        assert card.stat().st_size <= CARD_BUDGET_BYTES, f"{card.name} 超过 1.2KB"
        assert strategy_card(card.stem), f"{card.name} 内容为空"


def test_every_regime_prompt_within_10kb() -> None:
    for kind in REGIMES:
        for setup in (False, True):
            text = _prompt_for(kind, setup=setup)
            stats = prompt_size(text)
            assert stats["bytes"] <= GZ.MAX_PROMPT_BYTES, (kind, setup, stats)
            assert stats["estimated_tokens"] <= GZ.MAX_PROMPT_BYTES / GZ.CHARS_PER_TOKEN


def test_prompt_contains_required_sections() -> None:
    text = _prompt_for("broad_channel")
    assert "价格行为灰区复核" in text
    assert "程序已判定节点" in text
    assert "待复核问题" in text
    assert "输出契约" in text
    assert "JSON" in text
    assert "近端K线" in text
    # 人设三条硬禁令
    assert "不追" in text or "禁令" in text


def test_prompt_uses_contested_node_markers() -> None:
    text = _prompt_for("extreme_tr")
    assert "★" in text, "争议节点应被标记，便于模型优先复核"
    assert "§6.4" in text


def test_prompt_excludes_full_threshold_table() -> None:
    text = _prompt_for("normal_channel")
    for leaked in ("thresholds.py", "BARBWIRE_SCORE_MIN", "TIGHT_PULLBACK_MAX"):
        assert leaked not in text, f"prompt 不应包含代码阈值符号：{leaked}"
