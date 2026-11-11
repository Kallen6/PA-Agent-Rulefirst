"""感知层输出契约（P2 待实现）。

输出字段瘦身（v1 的 Stage1/Stage2 大部分字段已由代码产出）：
    cycle_position      : 9 类之一或 unknown
    direction           : bullish / bearish / neutral
    always_in           : bullish / bearish / none
    signal_bar_quality  : pass / fail / weak
    qualitative_notes   : 面向用户的解释文本

解析失败策略：返回 None 由调用方决定重试或降级，不做静默修补。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

ALLOWED_CYCLE_POSITIONS = (
    "spike",
    "micro_channel",
    "tight_channel",
    "normal_channel",
    "broad_channel",
    "trending_tr",
    "trading_range",
    "extreme_tr",
    "unknown",
)
ALLOWED_DIRECTIONS = ("bullish", "bearish", "neutral")
ALLOWED_ALWAYS_IN = ("bullish", "bearish", "none")
ALLOWED_QUALITY = ("pass", "fail", "weak")
ALLOWED_LEGIBILITY = ("clear", "confused", "unknown")


@dataclass(frozen=True)
class GrayZoneAnswer:
    cycle_position: str
    direction: str
    always_in: str
    signal_bar_quality: str
    qualitative_notes: str = ""
    confidence: float = 0.0  # 0–100；模型自评，仅用于记录与二次灰区
    # 可选感知字段：只影响胜率/禁令，不推翻代码周期与方向
    estimated_win_rate: float = 0.0
    win_rate_reason: str = ""
    reversal_attempt: bool = False
    wedge_type: str = "none"
    entry_quality: str = "unknown"
    second_entry: str = "unknown"
    # 「看不懂」出口（v1 §0.1 对齐）：confused → 撤销方案（wait），不推翻周期与方向
    market_legibility: str = "unknown"


ALLOWED_WEDGE_TYPES = ("none", "pullback", "reversal", "parabolic")
ALLOWED_ENTRY_QUALITY = ("strong", "weak", "invalid", "unknown")
ALLOWED_SECOND_ENTRY = ("first", "second", "third", "unknown")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "是", "1")
    return bool(value)


def parse_response(text: str) -> GrayZoneAnswer | None:
    """从模型文本中提取 JSON 并校验枚举；失败返回 None。"""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data: dict[str, Any] = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parse_payload(data)


def parse_payload(data: dict[str, Any]) -> GrayZoneAnswer | None:
    """校验已解析的字典（供客户端直接返回 dict 时复用）。"""
    if not isinstance(data, dict):
        return None
    cp = str(data.get("cycle_position", "unknown"))
    direction = str(data.get("direction", "neutral"))
    always_in = str(data.get("always_in", "none"))
    quality = str(data.get("signal_bar_quality", "weak"))
    if cp not in ALLOWED_CYCLE_POSITIONS:
        cp = "unknown"
    if direction not in ALLOWED_DIRECTIONS:
        direction = "neutral"
    if always_in not in ALLOWED_ALWAYS_IN:
        always_in = "none"
    if quality not in ALLOWED_QUALITY:
        quality = "weak"

    confidence = 0.0
    try:
        confidence = float(data.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if 0 < confidence <= 1:
        confidence *= 100.0
    confidence = max(0.0, min(100.0, confidence))

    win_rate = 0.0
    try:
        win_rate = float(data.get("estimated_win_rate", 0) or 0)
    except (TypeError, ValueError):
        win_rate = 0.0
    if 0 < win_rate <= 1:
        win_rate *= 100.0
    win_rate = max(0.0, min(100.0, win_rate))

    wedge_type = str(data.get("wedge_type", "none"))
    if wedge_type not in ALLOWED_WEDGE_TYPES:
        wedge_type = "none"
    entry_quality = str(data.get("entry_quality", "unknown"))
    if entry_quality not in ALLOWED_ENTRY_QUALITY:
        entry_quality = "unknown"
    second_entry = str(data.get("second_entry", "unknown"))
    if second_entry not in ALLOWED_SECOND_ENTRY:
        second_entry = "unknown"
    legibility = str(data.get("market_legibility", "unknown"))
    if legibility not in ALLOWED_LEGIBILITY:
        legibility = "unknown"

    return GrayZoneAnswer(
        cycle_position=cp,
        direction=direction,
        always_in=always_in,
        signal_bar_quality=quality,
        qualitative_notes=str(data.get("qualitative_notes", "")),
        confidence=confidence,
        estimated_win_rate=win_rate,
        win_rate_reason=str(data.get("win_rate_reason", "")),
        reversal_attempt=_as_bool(data.get("reversal_attempt", False)),
        wedge_type=wedge_type,
        entry_quality=entry_quality,
        second_entry=second_entry,
        market_legibility=legibility,
    )
