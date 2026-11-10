"""trace 结构：把 GateResult 转成与 v1 decision_trace 兼容的字典列表。

节点问题文案与 `prompt_engineering/二元决策.txt` 的 § 标题逐字对齐，
便于把 v1 记录与 v2 输出按 node_id 直接比对。
"""
from __future__ import annotations

import re
from typing import Any

from pa_engine.gates.base import GateResult

# 二元决策全部节点（§0–§11）。未实现的节点不出现在 trace 中，
# 但问题映射先补齐，避免日志里出现裸 node_id。
NODE_QUESTIONS: dict[str, str] = {
    "0.1": "是否看得懂当前市场？",
    "0.2": "是否具备继续深入分析的条件？",
    "0.3": "交易者方程（原则提醒）",
    "1.1": "数据是否足够？",
    "1.2": "是否能识别市场周期？",
    "1.3": "当前市场是否极端混乱？",
    "2.1": "是否存在明确惯性方向？",
    "2.2": "长程大背景方向与近期方向的关系？",
    "2.3": "当前方向是多头还是空头？",
    "2.4": "当前是否处于 Always In 状态？",
    "2.5": "当前惯性强度是否足够支持趋势跟踪？",
    "3.1": "是否满足尖峰条件？",
    "3.2": "尖峰是否仍在进行中？",
    "3.3": "尖峰进行中是否允许追？",
    "3.4": "尖峰是否出现第一次暂停或回撤？",
    "3.5": "尖峰后进入哪条路径？",
    "3.6": "是否出现高潮？",
    "3.7": "是否为第二次或第三次高潮？",
    "4.1": "是否出现有序波段结构？",
    "4.2": "通道方向是上涨还是下跌？",
    "5.1": "是否为微型通道？",
    "5.2": "是否为紧凑/窄通道？",
    "5.3": "是否为常规通道？",
    "5.4": "是否为宽通道/台阶？",
    "5.5": "是否为收缩台阶？",
    "6.1": "是否存在清晰上下边界？",
    "6.2": "是普通交易区间还是趋势型交易区间？",
    "6.3": "当前价格是否在区间边界？",
    "6.4": "是否出现 Barbwire/极窄区间？",
    "6.5": "区间是否出现突破？",
    "6.6": "是否为三角形形态？",
    "7.1": "是否只有单根反转K线？",
    "7.2": "是否同时满足两大反转条件？",
    "7.3": "是否出现二次入场？",
    "7.4": "二次入场是否失败？",
    "8.1": "是否有三推结构？",
    "8.2": "楔形是回撤还是反转？",
    "8.3": "是否为抛物线楔形？",
    "9.0": "信号棒是否已经收盘且质量足够？",
    "9.0P": "背景驱动限价单评估",
    "9.1": "信号K线是否已经收盘？",
    "9.2": "信号K线方向是否与计划方向一致？",
    "9.3": "信号棒是否过长？",
    "9.4": "是否是第一次入场？",
    "9.5": "是否有跟随？",
    "9.6": "是否为二次入场？",
    "9.7": "入场棒是否强势且有跟随？",
    "10.1": "是否能明确止损？",
    "10.2": "止损是否过大？",
    "10.3": "交易者方程是否通过？",
    "11.1": "是趋势/尖峰状态吗？",
    "11.2": "是通道回撤吗？",
    "11.3": "是交易区间吗？",
    "11.4": "是突破交易吗？",
    "14": "是否触犯禁止行为清单？",
}

_STAGE1_MAJORS = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 8})
_STAGE2_MAJORS = frozenset({9, 10, 11, 14})

_NODE_RE = re.compile(r"^(\d+)(?:\.(\d+))?(.*)$")


def build_trace(results: list[GateResult]) -> list[dict[str, Any]]:
    """按 node_id 顺序输出 trace 字典列表（字段与 v1 decision_trace 兼容）。"""
    ordered = sorted(results, key=lambda r: _node_sort_key(r.node_id))
    return [r.to_trace() for r in ordered]


def split_traces(results: list[GateResult]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """拆成 (阶段一 gate_trace, 阶段二 decision_trace)。"""
    stage1 = [r for r in results if _major(r.node_id) in _STAGE1_MAJORS]
    stage2 = [r for r in results if _major(r.node_id) in _STAGE2_MAJORS]
    return build_trace(stage1), build_trace(stage2)


def _major(node_id: str) -> int:
    return _node_sort_key(node_id)[0]


def _node_sort_key(node_id: str) -> tuple[int, float, str]:
    match = _NODE_RE.match(str(node_id).strip())
    if not match:
        return 999, 0.0, str(node_id)
    major = int(match.group(1))
    minor = float(match.group(2)) if match.group(2) else 0.0
    return major, minor, match.group(3) or ""
