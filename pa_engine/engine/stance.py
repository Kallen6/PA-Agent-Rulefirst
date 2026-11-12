"""下单倾向档位（v2 代码化实现）。

背景：v1 的四档（保守/均衡/激进/极度激进）以 40–60 行中文文案注入 Stage-2
提示词，数值阈值完全不变（``min_risk_reward_ratio()`` 直接丢弃档位参数）。
v2 改为阈值向量，档位只驱动 §10 的两个数值门槛，不向 prompt 增加任何文本。

明确不实现的四项及原因：
- 灰区带宽联动：会改变 LLM 调用次数与耗时，``band`` 仍独立配置；
- ``require_signal_bar``：会砍掉 §9.0P 背景限价路径，违反「不砍入场路径」；
- ``allow_neutral_entry`` / ``force_order``：v2 的 ``order_type`` 由
  ``gates.order.route_from_context()`` 纯代码决定，``direction=neutral`` 即
  ``NO_ORDER``，LLM 无权创造订单；放开等于让下单与方向判定脱钩，属高风险改动。

传递方式：``decide(stance=...)`` 归一化后写入 ``GateContext.extras["stance"]``，
闸门通过 :func:`stance_of` 读取。这样闸门签名不变，既有注册表与测试不受冲击，
``ctx=None`` / v1 记录回放一律回落到默认档。
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from pa_engine.config.thresholds import Stance
from pa_engine.gates.base import GateContext

#: 兼容中文/别名输入；归一化只做映射，不抛错（批处理不中断）。
_ALIASES: dict[str, str] = {
    "conservative": "conservative",
    "保守": "conservative",
    "balanced": "balanced",
    "均衡": "balanced",
    "aggressive": "aggressive",
    "激进": "aggressive",
    "extreme_aggressive": "extreme_aggressive",
    "extreme": "extreme_aggressive",
    "极度激进": "extreme_aggressive",
}


@dataclass(frozen=True)
class StancePolicy:
    """一档下单倾向的生效参数（不可变，可直接写入记录）。"""

    name: str
    label_zh: str
    min_rr: float
    min_equation_gap: float
    target_rr: float

    def as_record(self) -> dict[str, Any]:
        """记录用快照：回放时据此确认「当时用的是哪一档、门槛是多少」。"""
        return {
            "name": self.name,
            "label_zh": self.label_zh,
            "min_rr": self.min_rr,
            "min_equation_gap": self.min_equation_gap,
            "target_rr": self.target_rr,
        }


def is_valid_stance(value: Any) -> bool:
    """是否是可识别的档位名（含中文别名）；供 Web/CLI 做显式校验。"""
    return str(value or "").strip().lower() in _ALIASES or str(value or "").strip() in _ALIASES


def normalize_stance(value: Any) -> str:
    """把配置/请求里的档位名归一到 ``Stance.ORDER`` 之一；未知值回落默认档。"""
    text = str(value or "").strip()
    if not text:
        return Stance.DEFAULT
    return _ALIASES.get(text, _ALIASES.get(text.lower(), Stance.DEFAULT))


@lru_cache(maxsize=None)
def policy_of(name: str) -> StancePolicy:
    """按档位名取参数；未知档回落 ``Stance.DEFAULT``。"""
    key = normalize_stance(name)
    params = Stance.PARAMS[key]
    return StancePolicy(
        name=key,
        label_zh=Stance.LABELS_ZH[key],
        min_rr=float(params["min_rr"]),
        min_equation_gap=float(params["min_equation_gap"]),
        target_rr=float(params["target_rr"]),
    )


def stance_of(ctx: GateContext | None) -> StancePolicy:
    """读取上下文里的档位；未注入/被污染时回落到默认档。"""
    if ctx is not None:
        policy = ctx.extras.get("stance")
        if isinstance(policy, StancePolicy):
            return policy
    return policy_of(Stance.DEFAULT)


def apply_stance(ctx: GateContext, name: Any) -> StancePolicy:
    """归一化后写入上下文，返回生效策略。"""
    policy = policy_of(normalize_stance(name))
    ctx.extras["stance"] = policy
    return policy


def all_stances() -> list[dict[str, Any]]:
    """全部档位及其参数（供前端渲染选项、文档与测试遍历）。"""
    return [policy_of(name).as_record() for name in Stance.ORDER]


__all__ = [
    "StancePolicy",
    "all_stances",
    "apply_stance",
    "is_valid_stance",
    "normalize_stance",
    "policy_of",
    "stance_of",
]
