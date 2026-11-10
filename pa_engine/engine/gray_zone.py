"""灰区检测器（v2 核心增量）。

回答一个代码独有的问题：**这次分析需不需要请 LLM？**

判定规则（任一成立即 need_llm）：
1. 关键节点缺失（未实现或未运行）→ 灰区；
2. 关键节点置信度低于下限 → 灰区（A 类非关键节点的置信度只作展示，不触发）；
3. 主判测量值落在阈值 ±BAND 相对带宽内 → 灰区；
4. 节点结论冲突（如尖峰与紧凑通道同时成立、突破与铁丝网并存）→ 灰区。

「主判测量值」指每个节点的第一判据（如 §5.2 的回撤深度），辅助判据
（重叠率、趋势棒比例）只作证据展示，避免边界带把所有通道都判成灰区。
"""
from __future__ import annotations

from dataclasses import dataclass

from pa_engine.config.thresholds import Channel, GrayZone as GZ, Structure
from pa_engine.gates.base import GateResult


@dataclass(frozen=True)
class Measurement:
    """一个「值 vs 阈值」的测量，用于边界检测。"""

    name: str
    value: float
    threshold: float
    node_id: str = ""


@dataclass(frozen=True)
class GrayZoneVerdict:
    need_llm: bool
    contested_nodes: tuple[str, ...]
    reasons: tuple[str, ...]


def measurement_margin(m: Measurement) -> float:
    """测量的相对余量：|value - threshold| / |threshold|。"""
    base = abs(m.threshold) if m.threshold else 1.0
    return abs(m.value - m.threshold) / base


def contested_measurements(
    measurements: list[Measurement] | tuple[Measurement, ...],
    band: float = GZ.BAND,
) -> list[tuple[str, float]]:
    """返回落在阈值带宽内的测量：(名称, 相对余量)。"""
    out: list[tuple[str, float]] = []
    for m in measurements:
        margin = measurement_margin(m)
        if margin <= band:
            out.append((m.name, round(margin, 4)))
    return out


def closest_margin(
    measurements: list[Measurement] | tuple[Measurement, ...],
) -> tuple[str, float] | None:
    """返回距阈值最近的测量（不限带宽），用于分歧归因。"""
    best: tuple[str, float] | None = None
    for m in measurements:
        margin = measurement_margin(m)
        if best is None or margin < best[1]:
            best = (m.name, round(margin, 4))
    return best


_CHANNELS = ("micro_channel", "tight_channel", "normal_channel", "broad_channel")
_RANGES = ("trading_range", "trending_tr", "extreme_tr")


def measurements_from_results(
    results: list[GateResult], *, cycle_position: str = ""
) -> list[Measurement]:
    """从闸门 evidence 自动提取主判测量值（无需调用方手工维护）。

    `cycle_position` 用于过滤无关节点：通道状态下不检查区间边界测量，
    区间状态下不检查通道回撤测量，避免「新高的宽通道必然贴近上边界」这类伪灰区。
    """
    by_node = {r.node_id: r for r in results}
    out: list[Measurement] = []
    include_channels = not cycle_position or cycle_position in _CHANNELS
    include_ranges = not cycle_position or cycle_position in _RANGES

    def add(node: str, name: str, value, threshold) -> None:
        if value is None or threshold is None:
            return
        try:
            out.append(Measurement(f"{node}:{name}", float(value), float(threshold), node))
        except (TypeError, ValueError):
            return

    if "3.1" in by_node:
        ev = by_node["3.1"].evidence
        add("3.1", "body_overlap", ev.get("body_overlap"), ev.get("overlap_threshold"))
    if include_channels and "5.2" in by_node:
        add("5.2", "pullback_pct", by_node["5.2"].evidence.get("pullback_pct"), Channel.TIGHT_PULLBACK_MAX)
    if include_channels and "5.3" in by_node:
        ev = by_node["5.3"].evidence
        pct = ev.get("pullback_pct")
        add("5.3", "pullback_pct_min", pct, Channel.NORMAL_PULLBACK_MIN)
        add("5.3", "pullback_pct_max", pct, Channel.NORMAL_PULLBACK_MAX)
    if include_channels and "5.4" in by_node:
        ev = by_node["5.4"].evidence
        pct = ev.get("pullback_pct")
        add("5.4", "pullback_pct_min", pct, Channel.BROAD_PULLBACK_MIN)
        add("5.4", "pullback_pct_max", pct, Channel.BROAD_PULLBACK_MAX)
        add("5.4", "overlap_mean", ev.get("overlap_mean"), Channel.BROAD_OVERLAP_MIN)
    if include_ranges and "6.3" in by_node:
        position = by_node["6.3"].evidence.get("price_position")
        add("6.3", "position_lower", position, 1 / 3)
        add("6.3", "position_upper", position, 2 / 3)
    if include_ranges and "6.4" in by_node:
        ev = by_node["6.4"].evidence
        add("6.4", "barbwire_score", ev.get("barbwire_score"), Structure.BARBWIRE_SCORE_MIN)
        add("6.4", "range_width_atr", ev.get("range_width_atr"), Structure.BARBWIRE_WIDTH_ATR_MAX)
    if "9.3" in by_node:
        ev = by_node["9.3"].evidence
        add("9.3", "range_atr", ev.get("range_atr"), ev.get("threshold_atr"))
    if "10.2" in by_node:
        ev = by_node["10.2"].evidence
        risk = ev.get("risk")
        limits = ev.get("limits") or {}
        add("10.2", "channel_limit", risk, limits.get("channel"))
        add("10.2", "range_limit", risk, limits.get("range"))
        add("10.2", "atr_limit", risk, limits.get("atr"))
        # RR 不参与边界检测：外扩止损会把 RR 固定到 1.0，恒定贴阈值没有信息量
    return out


def detect_conflicts(
    results: list[GateResult],
    *,
    direction: str = "neutral",
    relationship: str = "neutral",
) -> list[tuple[str, str, tuple[str, ...]]]:
    """返回 (冲突键, 说明, 涉及节点)。只收敛真正互斥的结论。"""
    by_node = {r.node_id: r for r in results}
    conflicts: list[tuple[str, str, tuple[str, ...]]] = []

    if _true(by_node, "3.1") and _true(by_node, "5.2"):
        conflicts.append(("spike_vs_tight_channel", "尖峰与紧凑通道同时成立", ("3.1", "5.2")))
    if _true(by_node, "6.5") and _true(by_node, "6.4"):
        conflicts.append(("breakout_vs_barbwire", "铁丝网中出现突破信号", ("6.4", "6.5")))
    if "5.4" in by_node and by_node["5.4"].branch == "trending_tr" and _true(by_node, "6.4"):
        conflicts.append(("trending_tr_vs_extreme", "趋势型区间与极端区间冲突", ("5.4", "6.4")))
    if direction in ("bullish", "bearish") and relationship == "conflict":
        conflicts.append(("direction_vs_background", "近期方向与长程背景相反", ()))
    return conflicts


def _true(by_node: dict[str, GateResult], node_id: str) -> bool:
    return node_id in by_node and by_node[node_id].answer is True


def evaluate(
    results: list[GateResult],
    *,
    measurements: list[Measurement] | tuple[Measurement, ...] = (),
    band: float = GZ.BAND,
    critical_nodes: tuple[str, ...] = GZ.CRITICAL_NODES,
    conflicts: list[tuple[str, str, tuple[str, ...]]] | tuple = (),
) -> GrayZoneVerdict:
    by_node = {r.node_id: r for r in results}
    contested: list[str] = []
    reasons: list[str] = []

    for node in critical_nodes:
        if node not in by_node:
            contested.append(node)
            reasons.append(f"关键节点 {node} 缺失（未实现或未运行）")

    for node in critical_nodes:
        result = by_node.get(node)
        if result is not None and result.confidence < GZ.MIN_CONFIDENCE:
            contested.append(node)
            reasons.append(
                f"关键节点 {node} 置信度 {result.confidence:.2f} < {GZ.MIN_CONFIDENCE}"
            )

    for name, margin in contested_measurements(measurements, band):
        reasons.append(f"测量 {name} 距阈值余量 {margin:.1%}（band={band:.0%}）")
        node = name.split(":", 1)[0]
        if node in by_node:
            contested.append(node)

    for key, text, nodes in conflicts:
        contested.extend(nodes)
        reasons.append(f"结论冲突 {key}：{text}")

    unique = tuple(dict.fromkeys(contested))
    return GrayZoneVerdict(
        need_llm=bool(unique or reasons),
        contested_nodes=unique,
        reasons=tuple(reasons),
    )
