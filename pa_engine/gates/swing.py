"""§4 有序波段结构（B 类证据节点，不改变周期分类）。

节点映射（二元决策.txt §4）：
    §4.1 是否出现有序波段结构？  swing_structure=HH+HL / LL+LH 且摆动点足够
    §4.2 通道方向是上涨还是下跌？ 复用预填方向 / 摆动结构

这两个节点只补 trace 证据，不参与 `classify_cycle`，避免把 8 类合成行情
的周期判定改坏。
"""
from __future__ import annotations

from pa_engine.config.thresholds import Structure
from pa_engine.gates.base import (
    GateContext,
    GateResult,
    FrameLike,
    register,
    structure_of,
)

@register("4.1")
def gate_4_1(frame: FrameLike, ctx: GateContext) -> GateResult:
    sf = structure_of(frame, ctx)
    structure = sf.swing_structure
    ordered = structure in ("HH+HL", "LL+LH")
    swings = len(sf.swings)
    enough = swings >= Structure.SWING_MIN_COUNT
    answer = bool(ordered and enough)
    confidence = 0.85 if ordered and enough else (0.6 if ordered else 0.85)
    return GateResult(
        node_id="4.1",
        answer=answer,
        branch=structure if answer else ("insufficient" if not enough else "no"),
        confidence=confidence,
        evidence={
            "swing_structure": structure,
            "swings": swings,
            "lookback": Structure.LOOKBACK,
        },
        reason=(
            f"摆动结构={structure}、{swings}个枢轴"
            f"→{'有序波段' if answer else '未形成有序波段'}"
        ),
    )


@register("4.2")
def gate_4_2(frame: FrameLike, ctx: GateContext) -> GateResult:
    direction = str(ctx.extras.get("direction") or "neutral")
    if direction not in ("bullish", "bearish"):
        sf = structure_of(frame, ctx)
        direction = {
            "HH+HL": "bullish",
            "LL+LH": "bearish",
        }.get(sf.swing_structure, "neutral")
    return GateResult(
        node_id="4.2",
        answer=direction,
        branch=direction,
        confidence=0.85 if direction != "neutral" else 0.6,
        evidence={"direction": direction, "swing_structure": structure_of(frame, ctx).swing_structure},
        reason=f"通道/波段方向={direction}",
    )
