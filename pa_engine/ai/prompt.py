"""灰区提示词组装：把「可量化判断」留在代码里，只给模型感知类信息。

单次输入预算 ≤10KB（`GrayZone.MAX_PROMPT_BYTES`），构成：
    1. 人设与硬禁令（00_persona.txt，约 0.5KB）
    2. 当前状态策略卡（prompts/strategies/{cycle}.md，约 0.5KB）
    3. C 类节点速查（30_c_class_briefs.md，约 1.1KB）
    4. 程序已判定节点 + 争议节点（只读，禁止重复推理）
    5. 近端 K10–K1 紧凑表（可降级到 K6–K1）
    6. 输出契约（40_output_contract.md）

超预算时的降级顺序：K 线表 → 程序证据行 → C 类速查 → 策略卡；
人设、争议节点、输出契约永不裁剪。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pa_engine.config.thresholds import GrayZone as GZ
from pa_engine.engine.gray_zone import GrayZoneVerdict
from pa_engine.features.render import render_evidence_block
from pa_engine.gates.base import GateResult, FrameLike

PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"

HEADER = "# 价格行为灰区复核（感知类节点）\n"
CONSTRAINT = (
    "机械节点已由程序判定并有证据，不要重复推理、不要推翻，除非你能指出"
    "近端 K 线中程序证据未覆盖的反例。只回答下面列出的待复核问题。\n"
)

_CLASS_BRIEF_LIMIT = 1600
_GATE_LINE_LIMIT = 32


@lru_cache(maxsize=64)
def _read(name: str) -> str:
    path = PROMPT_DIR / name
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def strategy_card(cycle_position: str) -> str:
    """返回状态卡；未知状态回退到区间卡（最常见的默认状态）。"""
    name = cycle_position if cycle_position in _CARD_NAMES else "trading_range"
    return _read(f"strategies/{name}.md")


_CARD_NAMES = {
    "spike",
    "micro_channel",
    "tight_channel",
    "normal_channel",
    "broad_channel",
    "trending_tr",
    "trading_range",
    "extreme_tr",
}


def _evidence_lines(results: list[GateResult], gray: GrayZoneVerdict) -> list[str]:
    contested = set(gray.contested_nodes)
    ordered = sorted(results, key=lambda r: (r.node_id not in contested, r.node_id))
    lines = ["## 程序已判定节点（只读）"]
    for result in ordered[:_GATE_LINE_LIMIT]:
        mark = "★" if result.node_id in contested else "-"
        answer = result.answer
        if isinstance(answer, bool):
            answer = "是" if answer else "否"
        lines.append(
            f"{mark} §{result.node_id} {answer}/{result.branch}"
            f"（置信{result.confidence:.2f}）{result.reason}"
        )
    return lines


def anchor_block(anchor: dict) -> str:
    """「上次结论，仅参考」块；超预算时最先裁剪。"""
    previous = anchor.get("previous") or {}
    terminal = previous.get("terminal") or {}
    return "\n".join(
        [
            "## 上次结论（仅参考，不得推翻代码判定）",
            f"- 生成时间：{anchor.get('generated_at')}",
            f"- 新增已收盘K线：{anchor.get('new_bars')} 根",
            (
                f"- 上次：{previous.get('cycle_position')} / {previous.get('direction')}"
                f" / AlwaysIn={previous.get('always_in')} / gate={previous.get('gate_result')}"
                f" / {previous.get('order_type')}"
                f" / {terminal.get('outcome')}@{terminal.get('node_id')}"
            ),
        ]
    )


def _assemble(
    frame: FrameLike,
    results: list[GateResult],
    gray: GrayZoneVerdict,
    *,
    k_limit: int,
    include_briefs: bool,
    include_card: bool,
    cycle_position: str,
    include_anchor: bool = False,
    anchor: dict | None = None,
) -> str:
    parts: list[str] = [HEADER, _read("00_persona.txt"), CONSTRAINT]

    if include_card:
        card = strategy_card(cycle_position)
        if card:
            parts.append(f"## 当前状态策略卡（{cycle_position}）\n{card}")

    if include_anchor and anchor:
        parts.append(anchor_block(anchor))

    if include_briefs:
        briefs = _read("30_c_class_briefs.md")
        if briefs:
            parts.append(f"## C 类节点速查\n{briefs[:_CLASS_BRIEF_LIMIT]}")

    questions = ["## 待复核问题"]
    if gray.contested_nodes:
        questions.extend(f"- §{node}" for node in gray.contested_nodes)
    else:
        questions.append("- 无关键节点争议，请只复核下方证据是否支持代码结论")
    questions.extend(f"  原因：{reason}" for reason in gray.reasons[:6])
    parts.append("\n".join(questions))

    parts.append("\n".join(_evidence_lines(results, gray)))

    try:
        parts.append(render_evidence_block(frame, limit=k_limit))
    except Exception:  # 渲染失败不能阻断决策
        parts.append("## 近端K线\n（渲染失败，按程序节点判断）")

    parts.append(f"## 输出契约\n{_read('40_output_contract.md')}")
    return "\n\n".join(part for part in parts if part and part.strip())


def build_gray_zone_prompt(
    frame: FrameLike,
    results: list[GateResult],
    gray: GrayZoneVerdict,
    *,
    cycle_position: str = "",
    anchor: dict | None = None,
) -> str:
    """组装灰区 prompt，保证 UTF-8 字节数 ≤ MAX_PROMPT_BYTES。

    降级顺序：锚定块 → K 线表 → 程序证据行 → C 类速查 → 策略卡；
    人设、争议节点、输出契约始终保留。
    """
    if not cycle_position:
        stage1 = next((r for r in results if r.node_id == "1.2"), None)
        cycle_position = str(stage1.branch) if stage1 is not None else "unknown"

    attempts = [
        dict(k_limit=10, include_briefs=True, include_card=True, include_anchor=True),
        dict(k_limit=10, include_briefs=True, include_card=True, include_anchor=False),
        dict(k_limit=8, include_briefs=True, include_card=True, include_anchor=False),
        dict(k_limit=6, include_briefs=True, include_card=True, include_anchor=False),
        dict(k_limit=6, include_briefs=False, include_card=True, include_anchor=False),
        dict(k_limit=4, include_briefs=False, include_card=False, include_anchor=False),
    ]
    text = ""
    for options in attempts:
        text = _assemble(
            frame,
            results,
            gray,
            cycle_position=cycle_position,
            anchor=anchor,
            **options,
        )
        if len(text.encode("utf-8")) <= GZ.MAX_PROMPT_BYTES:
            return text

    # 最后一档仍超限：保留人设/争议/契约，截断中间证据
    return _hard_trim(text, GZ.MAX_PROMPT_BYTES)


def _hard_trim(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    keep = max_bytes - 32
    return encoded[: max(keep, 0)].decode("utf-8", errors="ignore") + "\n[输入已按预算截断]"


def prompt_size(text: str) -> dict[str, float | int]:
    raw = text.encode("utf-8")
    return {
        "bytes": len(raw),
        "chars": len(text),
        "estimated_tokens": round(len(text) / GZ.CHARS_PER_TOKEN, 1),
    }
