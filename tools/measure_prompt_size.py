"""对比 v1 提示词全量与 v2 精简集的体积/估算 token。

用法：
    python tools/measure_prompt_size.py

口径：
- token ≈ 字符数 / 1.6（中文保守估算）；
- v1 决策链路 = 市场诊断框架 + 二元决策 + 人设 + 4 种状态的分析识别/交易策略对；
- v2 灰区实测 = 8 类合成行情各组装一次 prompt，统计 min/avg/max。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pa_engine.ai.prompt import PROMPT_DIR, build_gray_zone_prompt
from pa_engine.config.thresholds import GrayZone as GZ
from pa_engine.engine.pipeline import decide

V1_ROOT = ROOT.parent / "PA_Agent" / "prompt_engineering"
V1_DECISION_FILES = (
    "市场诊断框架.txt",
    "二元决策.txt",
    "提示词大纲_人设与思维方式.txt",
    "极速上涨分析识别.txt",
    "极速上涨交易策略.txt",
    "极速下跌分析识别.txt",
    "极速下跌交易策略.txt",
    "上涨通道分析识别.txt",
    "上涨通道交易策略.txt",
    "下跌通道分析识别.txt",
    "下跌通道交易策略.txt",
    "震荡区间分析识别.txt",
    "震荡区间交易策略.txt",
)


def _stats(text: str) -> dict[str, float]:
    return {
        "bytes": len(text.encode("utf-8")),
        "chars": len(text),
        "tokens": round(len(text) / GZ.CHARS_PER_TOKEN, 1),
    }


def _read_v1(name: str) -> str:
    path = V1_ROOT / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def main() -> int:
    v1_full = ""
    if V1_ROOT.exists():
        for path in sorted(V1_ROOT.glob("*.txt")):
            v1_full += path.read_text(encoding="utf-8", errors="ignore")
    v1_decision = "".join(_read_v1(name) for name in V1_DECISION_FILES)

    core_names = (
        "00_persona.txt",
        "10_stage1_diagnosis.md",
        "20_stage2_execution.md",
        "30_c_class_briefs.md",
        "40_output_contract.md",
    )
    v2_core = "".join(
        (PROMPT_DIR / name).read_text(encoding="utf-8") for name in core_names
    )
    v2_cards = "".join(
        path.read_text(encoding="utf-8") for path in sorted((PROMPT_DIR / "strategies").glob("*.md"))
    )

    from tests.fixtures.synthetic import REGIMES, make_regime_rows
    from tests.support import build_v2_frame

    sizes = []
    for kind in REGIMES:
        frame = build_v2_frame(make_regime_rows(kind))
        outcome = decide(frame)
        text = build_gray_zone_prompt(frame, outcome.gate_results, outcome.gray_zone)
        sizes.append(len(text.encode("utf-8")))

    rows = [
        ("v1 全量语料", v1_full),
        ("v1 决策链路子集", v1_decision),
        ("v2 核心说明 5 份", v2_core),
        ("v2 核心 + 8 张策略卡", v2_core + v2_cards),
    ]
    print(f"{'项目':<24}{'字节':>10}{'字符':>10}{'估算token':>12}")
    for label, text in rows:
        s = _stats(text)
        print(f"{label:<24}{s['bytes']:>10.0f}{s['chars']:>10.0f}{s['tokens']:>12.0f}")

    print(f"\n灰区实测 prompt：min={min(sizes)}B avg={sum(sizes)/len(sizes):.0f}B max={max(sizes)}B")
    if v1_decision:
        reduction = 1 - max(sizes) / len(v1_decision.encode("utf-8"))
        print(f"相对 v1 决策链路子集，单次灰区输入缩减 {reduction:.1%}")
    print(f"预算上限 {GZ.MAX_PROMPT_BYTES}B；实测最大值{'未超限' if max(sizes) <= GZ.MAX_PROMPT_BYTES else '超限'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
