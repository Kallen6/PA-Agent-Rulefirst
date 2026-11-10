"""代码预填：方向、AlwaysIn、三窗口背景（不调用 LLM）。"""
from pa_engine.prefill.always_in import AlwaysInResult, judge_always_in
from pa_engine.prefill.direction import DirectionResult, judge_direction
from pa_engine.prefill.trend_context import build_trend_context, render_three_window_summary

__all__ = [
    "AlwaysInResult",
    "DirectionResult",
    "judge_always_in",
    "judge_direction",
    "build_trend_context",
    "render_three_window_summary",
]
