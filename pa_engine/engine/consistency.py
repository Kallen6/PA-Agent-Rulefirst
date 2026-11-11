"""灰区自一致性投票（P3 审计闭环 T3.3）。

只对**感知层字段**取样投票，绝不改写代码已确定的周期、方向与闸门结论：
- 每个字段按多数票取值；
- 多数占比低于 `MIN_AGREEMENT` 时，该字段降级为保守值（`cycle_position` → `unknown`）；
- 单次调用失败只计入 errors，不参与投票，不影响代码决策；
- 默认关闭（`decide(vote=0)`），由 CLI `--vote N` 显式开启。
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Protocol

# 3 次采样中 2 次一致（0.667）即为多数；低于该值视为无稳定共识。
MIN_AGREEMENT = 0.6

VOTE_FIELDS = (
    "cycle_position",
    "direction",
    "always_in",
    "signal_bar_quality",
    "wedge_type",
    "entry_quality",
    "second_entry",
    "reversal_attempt",
    "market_legibility",
)

# 分歧过大时的保守取值：宁可少下判断，也不放大感知层噪声
CONSERVATIVE: dict[str, Any] = {
    "cycle_position": "unknown",
    "direction": "neutral",
    "always_in": "none",
    "signal_bar_quality": "weak",
    "wedge_type": "none",
    "entry_quality": "unknown",
    "second_entry": "unknown",
    "reversal_attempt": False,
    "market_legibility": "unknown",
}


class VoteLLM(Protocol):
    def complete(self, prompt: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class VoteResult:
    """投票结果：多数答案 + 一致率 + 分歧字段。"""

    answer: dict[str, Any]
    samples: int
    valid: int
    agree_ratio: float
    disagreements: tuple[str, ...]
    degraded: tuple[str, ...]
    errors: tuple[str, ...]

    def as_record(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "valid": self.valid,
            "agree_ratio": self.agree_ratio,
            "disagreements": list(self.disagreements),
            "degraded": list(self.degraded),
            "errors": list(self.errors),
        }


def _normalize(reply: Any) -> dict[str, Any] | None:
    """复用主线契约解析，保证投票前后字段语义一致。"""
    from pa_engine.ai.contract import parse_payload

    if isinstance(reply, str):
        import json

        try:
            reply = json.loads(reply)
        except json.JSONDecodeError:
            return None
    if not isinstance(reply, dict):
        return None
    payload = reply.get("answer") if isinstance(reply.get("answer"), dict) else reply
    parsed = parse_payload(payload)
    if parsed is None:
        return None
    return {
        "cycle_position": parsed.cycle_position,
        "direction": parsed.direction,
        "always_in": parsed.always_in,
        "signal_bar_quality": parsed.signal_bar_quality,
        "qualitative_notes": parsed.qualitative_notes,
        "confidence": parsed.confidence,
        "estimated_win_rate": parsed.estimated_win_rate,
        "win_rate_reason": parsed.win_rate_reason,
        "reversal_attempt": parsed.reversal_attempt,
        "wedge_type": parsed.wedge_type,
        "entry_quality": parsed.entry_quality,
        "second_entry": parsed.second_entry,
        "market_legibility": parsed.market_legibility,
    }


def _majority(values: list[Any]) -> tuple[Any, int]:
    """返回值与最高票数（保留首次出现顺序，结果稳定）。"""
    counts: dict[Any, int] = {}
    first_seen: dict[Any, int] = {}
    for index, value in enumerate(values):
        key = value
        counts[key] = counts.get(key, 0) + 1
        first_seen.setdefault(key, index)
    best = max(counts.items(), key=lambda item: (item[1], -first_seen[item[0]]))
    return best[0], best[1]


def vote_gray_zone(
    llm: VoteLLM,
    prompt: str,
    *,
    samples: int = 3,
    min_agreement: float = MIN_AGREEMENT,
) -> VoteResult:
    """对灰区 prompt 采样 `samples` 次并做字段多数投票。"""
    total = max(1, int(samples))
    replies: list[dict[str, Any]] = []
    errors: list[str] = []
    for _ in range(total):
        try:
            normalized = _normalize(llm.complete(prompt))
        except Exception as exc:  # 单次失败不影响其它采样
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
        if normalized is None:
            errors.append("契约解析失败")
            continue
        replies.append(normalized)

    if not replies:
        return VoteResult(
            answer={},
            samples=total,
            valid=0,
            agree_ratio=0.0,
            disagreements=tuple(VOTE_FIELDS),
            degraded=tuple(VOTE_FIELDS),
            errors=tuple(errors),
        )

    answer: dict[str, Any] = {}
    disagreements: list[str] = []
    degraded: list[str] = []
    ratios: list[float] = []

    for field in VOTE_FIELDS:
        values = [reply[field] for reply in replies]
        winner, count = _majority(values)
        ratio = count / len(values)
        ratios.append(ratio)
        if ratio < min_agreement:
            answer[field] = CONSERVATIVE[field]
            disagreements.append(field)
            degraded.append(field)
        else:
            answer[field] = winner

    # 数值字段取中位数：胜率只影响交易者方程，不参与几何计算
    win_rates = [float(reply.get("estimated_win_rate") or 0.0) for reply in replies]
    win_rates = [value for value in win_rates if value > 0]
    answer["estimated_win_rate"] = round(float(median(win_rates)), 1) if win_rates else 0.0

    reasons = [str(reply.get("win_rate_reason") or "") for reply in replies]
    answer["win_rate_reason"] = next((text for text in reasons if text), "")

    confidences = [float(reply.get("confidence") or 0.0) for reply in replies]
    answer["confidence"] = round(float(median(confidences)), 1) if confidences else 0.0

    notes = [str(reply.get("qualitative_notes") or "") for reply in replies]
    answer["qualitative_notes"] = next((text for text in notes if text), "")

    return VoteResult(
        answer=answer,
        samples=total,
        valid=len(replies),
        # 取最低字段一致率（worst-case）：任何一个字段不稳，整体共识就不可信
        agree_ratio=round(min(ratios), 4) if ratios else 0.0,
        disagreements=tuple(disagreements),
        degraded=tuple(dict.fromkeys(degraded)),
        errors=tuple(errors),
    )
