"""§14 禁止行为守卫（最终硬闸门）。

汇总散落在各节的硬禁令，作为下单前最后一道检查：
- 尖峰进行中追单（§3.3）
- 逆势 / 反转尝试方向与长程背景相反（§7）
- 楔形反转、抛物线楔形追单（§8.2/§8.3）
- 二次入场失败后的第三次尝试（§7.4/§9.6）
- 区间中部、铁丝网（§9.0P/§11.3）
- 计划型限价缺少结构止损（§9.0P）

答案语义与其它节点相反：`answer=True` 表示**触犯**禁令，流水线立即撤销方案。
"""
from __future__ import annotations

from pa_engine.config.thresholds import Entry
from pa_engine.gates.base import GateContext, GateResult, FrameLike, register, structure_of


def collect_violations(frame: FrameLike, ctx: GateContext) -> list[str]:
    flags: list[str] = []
    cycle = str(ctx.extras.get("cycle_position") or "unknown")
    direction = str(ctx.extras.get("direction") or "neutral")
    always_in = str(ctx.extras.get("always_in") or "none")
    zone = str(ctx.extras.get("zone") or "unknown")
    entry_style = str(ctx.extras.get("entry_style") or "signal")

    if cycle == "spike" and str(ctx.extras.get("spike_stage") or "") == "ongoing":
        flags.append("spike_chase")
    if direction in ("bullish", "bearish") and always_in in ("bullish", "bearish"):
        if direction != always_in:
            flags.append("counter_trend_always_in")
    # 「逆势」以 Always In（近端主判）为准；长程背景仅作参考（v1 §2.4）。
    if (
        ctx.extras.get("reversal_attempt") is True
        and always_in in ("bullish", "bearish")
        and direction in ("bullish", "bearish")
        and direction != always_in
    ):
        flags.append("counter_trend_reversal")
    if str(ctx.extras.get("wedge_type") or "") == "reversal":
        flags.append("wedge_reversal_chase")
    if ctx.extras.get("parabolic") is True:
        flags.append("parabolic_chase")
    if cycle == "trading_range" and zone == "middle_third":
        flags.append("range_middle")
    if cycle == "extreme_tr":
        flags.append("barbwire")
    if entry_style == "background_limit" and str(ctx.extras.get("stop_reference") or "") != "structure":
        flags.append("no_structure_stop")

    if direction in ("bullish", "bearish"):
        sf = structure_of(frame, ctx)
        count = sf.hl_count.bull_count if direction == "bullish" else sf.hl_count.bear_count
        if count >= Entry.THIRD_ENTRY_MIN_HL:
            flags.append("third_attempt")
    return flags


@register("14")
def gate_14(frame: FrameLike, ctx: GateContext) -> GateResult:
    flags = collect_violations(frame, ctx)
    answer = bool(flags)
    return GateResult(
        node_id="14",
        answer=answer,
        branch=flags[0] if flags else "clear",
        confidence=0.95 if flags else 0.85,
        evidence={"violations": flags, "entry_style": ctx.extras.get("entry_style")},
        reason=(
            f"触犯禁止行为：{', '.join(flags)}" if flags else "未触犯 §14 禁止行为清单"
        ),
    )
