"""8 类合成行情的灰区比例与调用次数估算（架构验证，非统计结论）。

用法：
    python tools/synthetic_gray_ratio.py

输出：每类行情（原始 / 叠加合格信号棒）的周期、灰区、调用次数、prompt 体积、终态。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pa_engine.ai.client import MockLLM
from pa_engine.config.thresholds import GrayZone as GZ
from pa_engine.engine.pipeline import decide

from tests.fixtures.synthetic import REGIMES, make_regime_rows, make_setup_rows
from tests.support import build_v2_frame


def main() -> int:
    print(f"{'行情':<16}{'样本':<6}{'周期':<16}{'灰区':<5}{'调用':<4}{'promptB':>8}  {'下单':<6}{'终态':<7}")
    total = gray = calls = trades = 0
    max_prompt = 0
    for kind in REGIMES:
        for label, rows in (("原始", make_regime_rows(kind)), ("信号棒", make_setup_rows(kind))):
            outcome = decide(build_v2_frame(rows), llm=MockLLM())
            decision = outcome.decision
            total += 1
            gray += int(outcome.gray_zone.need_llm)
            calls += int(outcome.llm_called)
            trades += int(decision["terminal"]["outcome"] == "trade")
            prompt_bytes = int(outcome.prompt_stats.get("bytes", 0))
            max_prompt = max(max_prompt, prompt_bytes)
            print(
                f"{kind:<16}{label:<6}{decision['cycle_position']:<16}"
                f"{'是' if outcome.gray_zone.need_llm else '否':<5}"
                f"{int(outcome.llm_called):<4}{prompt_bytes:>8}  "
                f"{decision['order_type']:<6}{decision['terminal']['outcome']:<7}"
            )

    print(
        f"\n样本 {total}，灰区 {gray}（{gray / total:.0%}），"
        f"LLM 调用 {calls}（{calls / total:.0%}），成交 {trades}"
    )
    print(f"最大 prompt {max_prompt}B / 预算 {GZ.MAX_PROMPT_BYTES}B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
