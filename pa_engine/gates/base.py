"""闸门函数基础设施：统一契约 + 注册表。

设计约束：
- 闸门只读快照，不做 IO、不调 LLM；
- 同一输入必须得到同一输出（可复现），否则不配做 A 类闸门；
- 每个闸门返回 GateResult，evidence 必须携带支撑答案的测量值，
  供 trace 展示与灰区检测共用。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class FrameLike(Protocol):
    """闸门所需的最小帧契约（与 v1 KlineFrame 结构兼容）。

    v1 的 KlineFrame 直接满足该协议：bars 为最新在前的 KlineBar 元组，
    indicators 提供 ema20 / atr14 序列。
    """

    bars: tuple[Any, ...]
    indicators: Any


@dataclass(frozen=True)
class GateContext:
    """一次闸门运行共享的派生量（避免重复计算）。"""

    atr: float | None = None
    ema: float | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GateResult:
    """单个二元节点的判定结果。"""

    node_id: str
    answer: bool | str
    branch: str                 # "yes" / "no" / ""（无分支语义）
    confidence: float           # 0.0–1.0，1.0 表示恒定或纯数值判定
    evidence: dict[str, Any]
    reason: str
    filled_by: str = "program"  # program = 代码判定；llm = 灰区复核

    def to_trace(self) -> dict[str, Any]:
        from pa_engine.engine.trace import NODE_QUESTIONS

        return {
            "node_id": self.node_id,
            "question": NODE_QUESTIONS.get(self.node_id, self.node_id),
            "answer": "是" if self.answer is True else "否" if self.answer is False else str(self.answer),
            "branch": self.branch,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "reason": self.reason,
            "filled_by": self.filled_by,
        }


GateFn = Callable[[FrameLike, GateContext], GateResult]

GATES: dict[str, GateFn] = {}


def register(node_id: str) -> Callable[[GateFn], GateFn]:
    """把闸门函数注册到全局表（import 即注册）。"""

    def deco(fn: GateFn) -> GateFn:
        if node_id in GATES:
            raise ValueError(f"节点 {node_id} 已注册：{GATES[node_id].__name__}")
        GATES[node_id] = fn
        return fn

    return deco


def run_gate(node_id: str, frame: FrameLike, ctx: GateContext | None = None) -> GateResult:
    """运行单个闸门；未注册即抛错（防止静默漏判）。"""
    try:
        fn = GATES[node_id]
    except KeyError as exc:
        raise KeyError(f"节点 {node_id} 尚未实现（未注册）") from exc
    return fn(frame, ctx or GateContext())


def run_all(
    frame: FrameLike,
    ctx: GateContext | None = None,
    nodes: list[str] | tuple[str, ...] | None = None,
) -> list[GateResult]:
    """按节点号顺序运行已注册闸门；nodes 为空时运行全部已注册节点。"""
    ctx = ctx or GateContext()
    targets = list(nodes) if nodes is not None else sorted(GATES)
    return [run_gate(n, frame, ctx) for n in targets]


# ── 共享派生量（懒计算并缓存在 GateContext.extras） ────────────────────────────


def structure_of(frame: FrameLike, ctx: GateContext):
    """返回 SimpleMarketFeatures，同一 ctx 内只计算一次。"""
    cached = ctx.extras.get("sf")
    if cached is not None:
        return cached
    from pa_engine.features.structure import compute_simple_market_features

    sf = compute_simple_market_features(frame)
    ctx.extras["sf"] = sf
    return sf


def kline_features_of(frame: FrameLike, ctx: GateContext):
    cached = ctx.extras.get("kf")
    if cached is not None:
        return cached
    from pa_engine.features.kline import compute_kline_geometry_features

    kf = compute_kline_geometry_features(frame)
    ctx.extras["kf"] = kf
    return kf


def atr_of(frame: FrameLike, ctx: GateContext) -> float | None:
    if ctx.atr is not None:
        return ctx.atr
    indicators = getattr(frame, "indicators", None)
    atr14 = getattr(indicators, "atr14", ()) or ()
    if atr14 and not math.isnan(float(atr14[0])) and float(atr14[0]) > 0:
        return float(atr14[0])
    return None


def ema_of(frame: FrameLike, ctx: GateContext) -> float | None:
    if ctx.ema is not None:
        return ctx.ema
    indicators = getattr(frame, "indicators", None)
    ema20 = getattr(indicators, "ema20", ()) or ()
    if ema20 and not math.isnan(float(ema20[0])):
        return float(ema20[0])
    return None
