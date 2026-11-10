"""恒定节点闸门（P0 已实现）。

这两个节点在当前架构下是逻辑常量，不需要任何 K 线输入：
- §3.3 尖峰追单：prompt 原文标注「硬规则，无例外」（SCS 已废止）→ 恒为否；
- §9.1 信号K线收盘：v1 快照构建有意排除 forming bar，K1 恒为已收盘 → 恒为是。

把它们交给 LLM 只会浪费 token，并引入「模型可能违反硬规则」的风险。
"""
from __future__ import annotations

from pa_engine.gates.base import GateContext, GateResult, FrameLike, register


@register("3.3")
def gate_3_3(frame: FrameLike, ctx: GateContext) -> GateResult:
    return GateResult(
        node_id="3.3",
        answer=False,
        branch="no",
        confidence=1.0,
        evidence={"rule": "SCS 已废止，禁止尖峰追单（二元决策.txt §3.3 硬规则）"},
        reason="恒定规则：尖峰进行中不允许追单",
    )


@register("9.1")
def gate_9_1(frame: FrameLike, ctx: GateContext) -> GateResult:
    return GateResult(
        node_id="9.1",
        answer=True,
        branch="yes",
        confidence=1.0,
        evidence={"rule": "快照构建已排除 forming bar，K1 恒为已收盘"},
        reason="恒定规则：信号K线必然已收盘",
    )
