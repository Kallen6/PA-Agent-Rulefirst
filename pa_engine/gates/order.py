"""下单方式路由闸门（§11，查表 + 少量状态细化）。

节点映射（二元决策.txt §11，节点号与 v1 一致）：
    §11.1 趋势/尖峰 → 回撤限价；顺向旗形突破用突破单；尖峰进行中不下单（§3.3）
    §11.2 通道       → 微型/紧凑限价（前棒极点）；常规突破单；宽通道限价
    §11.3 交易区间   → 边界限价；中部不挂单；趋势型区间用突破单
    §11.4 突破交易   → 已确认突破（surviving/close_breakout）用突破单

与 v1 的差异（v2 刻意收紧）：
- v1 `_CYCLE_ORDER_METHOD` 把 spike 映射为市价单；v2 沿用精简提示词的硬禁令
  「禁止市价追单」，尖峰只在回撤/旗形确认后入场；
- 所有下单方式只输出 限价单 / 突破单 / 不下单，不输出市价单。
"""
from __future__ import annotations

from dataclasses import dataclass

from pa_engine.gates.base import GateContext, GateResult, FrameLike, register

NO_ORDER = "不下单"
LIMIT_ORDER = "限价单"
STOP_ORDER = "突破单"

_CHANNELS = ("micro_channel", "tight_channel", "normal_channel", "broad_channel")


@dataclass(frozen=True)
class OrderRoute:
    node_id: str
    order_type: str
    reason: str
    basis: str
    fallback: bool = False
    entry_style: str = "signal"


def route_order(
    *,
    cycle_position: str,
    direction: str = "neutral",
    spike_stage: str = "none",
    spike_path: str = "none",
    breakout_quality: str = "none",
    zone: str = "unknown",
    entry_style: str = "signal",
) -> OrderRoute:
    """按状态路由下单方式（纯查表，无副作用）。

    `entry_style=signal` 为信号棒路径（沿用原路由表）；
    `entry_style=background_limit` 为 §9.0P 背景限价路径：
    常规通道/趋势型区间允许边界限价，但区间中部、极端区间、方向中性仍不下单。
    """
    style = "background_limit" if entry_style == "background_limit" else "signal"
    cycle = cycle_position or "unknown"
    if direction not in ("bullish", "bearish") or cycle in ("unknown",):
        return OrderRoute("", NO_ORDER, "方向未定或周期未知，不下单", "direction", entry_style=style)
    if cycle == "extreme_tr":
        return OrderRoute("11.3", NO_ORDER, "极端区间（Barbwire）不交易", "cycle", entry_style=style)

    if cycle == "spike":
        if spike_stage == "ongoing":
            return OrderRoute(
                "11.1", NO_ORDER, "尖峰进行中禁止追单（§3.3），等回撤", "spike_stage", entry_style=style
            )
        if spike_path == "flag":
            if style == "background_limit":
                return OrderRoute(
                    "11.1", NO_ORDER, "尖峰后旗形需突破确认，背景限价不适用", "spike_path", entry_style=style
                )
            return OrderRoute("11.1", STOP_ORDER, "尖峰后旗形：顺向突破单", "spike_path", entry_style=style)
        if spike_path == "channel":
            return OrderRoute("11.1", LIMIT_ORDER, "尖峰后浅回撤：回撤位限价", "spike_path", entry_style=style)
        if spike_path == "range":
            return OrderRoute("11.1", LIMIT_ORDER, "尖峰后深回撤转为区间：边界限价", "spike_path", entry_style=style)
        return OrderRoute(
            "11.1", LIMIT_ORDER, "尖峰已结束：等回撤限价（不追）", "spike_stage", entry_style=style
        )

    if cycle in _CHANNELS:
        if cycle in ("micro_channel", "tight_channel"):
            return OrderRoute("11.2", LIMIT_ORDER, "窄通道：前棒极点附近限价", "cycle", entry_style=style)
        if cycle == "normal_channel":
            if style == "background_limit":
                return OrderRoute(
                    "11.2", LIMIT_ORDER, "常规通道：回撤结构位限价（§9.0P）", "cycle", entry_style=style
                )
            return OrderRoute("11.2", STOP_ORDER, "常规通道：突破单等确认", "cycle", entry_style=style)
        return OrderRoute("11.2", LIMIT_ORDER, "宽通道：近边界限价", "cycle", entry_style=style)

    if cycle == "trending_tr":
        if style == "background_limit":
            return OrderRoute(
                "11.2", LIMIT_ORDER, "趋势型区间：顺趋势边界限价（§9.0P）", "cycle", entry_style=style
            )
        return OrderRoute("11.2", STOP_ORDER, "趋势型区间：顺趋势边界突破单", "cycle", entry_style=style)

    if cycle == "trading_range":
        if breakout_quality in ("surviving", "close_breakout"):
            return OrderRoute(
                "11.4", STOP_ORDER, "区间已出现确认突破：突破单（不追第一棒）", "breakout", entry_style=style
            )
        if zone == "middle_third":
            return OrderRoute("11.3", NO_ORDER, "区间中部不挂单", "zone", entry_style=style)
        if zone == "lower_third" and direction == "bullish":
            return OrderRoute("11.3", LIMIT_ORDER, "区间下边界：做多限价", "zone", entry_style=style)
        if zone == "upper_third" and direction == "bearish":
            return OrderRoute("11.3", LIMIT_ORDER, "区间上边界：做空限价", "zone", entry_style=style)
        return OrderRoute(
            "11.3",
            NO_ORDER,
            f"边界侧别与方向不一致（{zone} vs {direction}），等待反向边界",
            "zone",
            entry_style=style,
        )

    return OrderRoute("", NO_ORDER, f"未覆盖的周期 {cycle}，不下单", "cycle", entry_style=style)


def route_from_context(ctx: GateContext) -> OrderRoute:
    """从 GateContext 读取状态并缓存路由结果。"""
    cached = ctx.extras.get("order_route")
    if isinstance(cached, OrderRoute):
        return cached
    stage1 = ctx.extras.get("stage1") or {}
    route = route_order(
        cycle_position=str(ctx.extras.get("cycle_position") or stage1.get("cycle_position") or "unknown"),
        direction=str(ctx.extras.get("direction") or "neutral"),
        spike_stage=str(ctx.extras.get("spike_stage") or "none"),
        spike_path=str(ctx.extras.get("spike_path") or "none"),
        breakout_quality=str(ctx.extras.get("breakout_quality") or "none"),
        zone=str(ctx.extras.get("zone") or "unknown"),
        entry_style=str(ctx.extras.get("entry_style") or "signal"),
    )
    ctx.extras["order_route"] = route
    return route


def _gate(node_id: str, frame: FrameLike, ctx: GateContext) -> GateResult:
    route = route_from_context(ctx)
    matched = route.node_id == node_id
    return GateResult(
        node_id=node_id,
        answer=matched,
        branch=route.order_type,
        confidence=0.9 if matched else 0.85,
        evidence={
            "cycle_position": ctx.extras.get("cycle_position"),
            "direction": ctx.extras.get("direction"),
            "spike_stage": ctx.extras.get("spike_stage"),
            "spike_path": ctx.extras.get("spike_path"),
            "breakout_quality": ctx.extras.get("breakout_quality"),
            "zone": ctx.extras.get("zone"),
            "order_type": route.order_type,
            "route_node": route.node_id,
            "basis": route.basis,
            "fallback": route.fallback,
        },
        reason=route.reason,
    )


@register("11.1")
def gate_11_1(frame: FrameLike, ctx: GateContext) -> GateResult:
    return _gate("11.1", frame, ctx)


@register("11.2")
def gate_11_2(frame: FrameLike, ctx: GateContext) -> GateResult:
    return _gate("11.2", frame, ctx)


@register("11.3")
def gate_11_3(frame: FrameLike, ctx: GateContext) -> GateResult:
    return _gate("11.3", frame, ctx)


@register("11.4")
def gate_11_4(frame: FrameLike, ctx: GateContext) -> GateResult:
    return _gate("11.4", frame, ctx)


__all__ = [
    "NO_ORDER",
    "LIMIT_ORDER",
    "STOP_ORDER",
    "OrderRoute",
    "route_order",
    "route_from_context",
    "gate_11_1",
    "gate_11_2",
    "gate_11_3",
    "gate_11_4",
]
