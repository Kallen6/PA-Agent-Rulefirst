"""§2.4 Always In 预填（从 v1 `judge_always_in` 移植）。

近端 K8–K1 为主判，背景 K20–K1 为参考（不否决近端）。
Gate1 加权同侧占比；Gate2 EMA 斜率；Gate3 摆动结构 + 浅回撤（强度标签）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from pa_engine.config.thresholds import Prefill
from pa_engine.data.types import KlineFrame
from pa_engine.prefill.direction import swing_prices


@dataclass(frozen=True)
class AlwaysInResult:
    always_in: str  # bullish / bearish / none
    answer: bool
    branch: str | None
    strength: str
    near_ratio: float
    bg_ratio: float
    reason: str


def judge_always_in(frame: KlineFrame) -> AlwaysInResult:
    bars = frame.bars
    ema20 = frame.indicators.ema20
    atr14 = frame.indicators.atr14
    n = max((int(b.seq) for b in bars), default=0) or len(bars)

    n_near = min(Prefill.ALWAYS_IN_NEAR_WINDOW, n)
    n_bg = min(Prefill.ALWAYS_IN_WINDOW, n)

    near = _eval_gates(
        frame,
        n_near,
        ema20,
        atr14,
        n,
        slope_lookback=min(5, max(n - 1, 1)),
        same_side=Prefill.ALWAYS_IN_NEAR_SAME_SIDE,
    )
    bg = _eval_gates(
        frame,
        n_bg,
        ema20,
        atr14,
        n,
        slope_lookback=Prefill.EMA_SLOPE_LOOKBACK,
        same_side=Prefill.ALWAYS_IN_SAME_SIDE,
    )

    if near["bull_core"]:
        return _result(
            "bullish",
            near,
            bg,
            strength="（结构确认，强AIL）" if near["gate3_bull"] else "（结构弱/回撤深，弱AIL）",
            label="Always In Long（AIL）",
            n_near=n_near,
            n_bg=n_bg,
        )
    if near["bear_core"]:
        return _result(
            "bearish",
            near,
            bg,
            strength="（结构确认，强AIS）" if near["gate3_bear"] else "（结构弱/回撤深，弱AIS）",
            label="Always In Short（AIS）",
            n_near=n_near,
            n_bg=n_bg,
        )
    if bg["bull_core"]:
        return _result(
            "bullish",
            near,
            bg,
            strength="（仅背景确认，近端未共振，弱AIL）",
            label="弱AIL",
            n_near=n_near,
            n_bg=n_bg,
        )
    if bg["bear_core"]:
        return _result(
            "bearish",
            near,
            bg,
            strength="（仅背景确认，近端未共振，弱AIS）",
            label="弱AIS",
            n_near=n_near,
            n_bg=n_bg,
        )

    reason = (
        f"【近端K{n_near}-K1】多侧{near['above']:.1%}/空侧{near['below']:.1%}；{near['slope_desc']}。"
        f"【背景K{n_bg}-K1】多侧{bg['above']:.1%}/空侧{bg['below']:.1%}；{bg['slope_desc']}。"
        "近端与背景均未达 Always In 阈值。"
    )
    return AlwaysInResult(
        always_in="none",
        answer=False,
        branch=None,
        strength="",
        near_ratio=near["above"] if near["above"] >= near["below"] else near["below"],
        bg_ratio=bg["above"] if bg["above"] >= bg["below"] else bg["below"],
        reason=reason,
    )


def _result(
    direction: str,
    near: dict,
    bg: dict,
    *,
    strength: str,
    label: str,
    n_near: int,
    n_bg: int,
) -> AlwaysInResult:
    ratio = near["above"] if direction == "bullish" else near["below"]
    bg_ratio = bg["above"] if direction == "bullish" else bg["below"]
    conflict = ""
    if direction == "bullish" and bg["bear_core"]:
        conflict = " ⚠️ 背景仍偏空，仅作阻力风险提示，不否决近端。"
    if direction == "bearish" and bg["bull_core"]:
        conflict = " ⚠️ 背景仍偏多，仅作支撑风险提示，不否决近端。"
    reason = (
        f"【近端K{n_near}-K1】加权同侧占比{ratio:.1%}；{near['slope_desc']}；"
        f"{near['swing_desc']}；{near['pullback_desc']}。判定为{label}{strength}。"
        f"【背景K{n_bg}-K1】多侧{bg['above']:.1%}/空侧{bg['below']:.1%}；{bg['slope_desc']}。{conflict}"
    )
    return AlwaysInResult(
        always_in=direction,
        answer=True,
        branch="AIL" if direction == "bullish" else "AIS",
        strength=strength,
        near_ratio=ratio,
        bg_ratio=bg_ratio,
        reason=reason,
    )


def _weighted_ema_side(frame: KlineFrame, window: int) -> tuple[float, float]:
    bars = frame.bars
    ema20 = frame.indicators.ema20
    w_above = w_below = 0.0
    for i, bar in enumerate(list(bars)[:window]):
        if i >= len(ema20):
            break
        try:
            ema_val = float(ema20[i])
            close_val = float(bar.close)
        except (TypeError, ValueError):
            continue
        if math.isnan(ema_val):
            continue
        weight = float(window - i)
        if close_val > ema_val:
            w_above += weight
        elif close_val < ema_val:
            w_below += weight
    return w_above, w_below


def _eval_gates(
    frame: KlineFrame,
    window: int,
    ema20: tuple[float, ...],
    atr14: tuple[float, ...],
    n: int,
    *,
    slope_lookback: int,
    same_side: float,
) -> dict:
    w_above, w_below = _weighted_ema_side(frame, window)
    valid = w_above + w_below
    above = w_above / valid if valid > 0 else 0.0
    below = w_below / valid if valid > 0 else 0.0

    slope_sign = 0
    slope_desc = "EMA斜率:0"
    try:
        if ema20 and not math.isnan(float(ema20[0])):
            k = min(slope_lookback, max(n - 1, 1))
            if k >= 1 and len(ema20) > k and not math.isnan(float(ema20[k])):
                d = float(ema20[0]) - float(ema20[k])
                atr = float(atr14[0]) if atr14 and not math.isnan(float(atr14[0])) else None
                thr = Prefill.DIRECTION_EMA_SLOPE_ATR * atr if atr else 0.0
                if d > thr:
                    slope_sign = 1
                    slope_desc = f"EMA向上(d={d:.4f})"
                elif d < -thr:
                    slope_sign = -1
                    slope_desc = f"EMA向下(d={d:.4f})"
                else:
                    slope_desc = f"EMA平坦(d={d:.4f})"
    except (TypeError, ValueError):
        pass

    swing_bull = swing_bear = False
    swing_desc = "波段:未验证"
    try:
        highs, lows = swing_prices(frame.bars, window)
        if len(highs) >= 2 and len(lows) >= 2:
            hh = highs[0] > highs[1]
            hl = lows[0] > lows[1]
            ll = lows[0] < lows[1]
            lh = highs[0] < highs[1]
            if hh and hl:
                swing_bull = True
                swing_desc = "波段HH+HL✓"
            elif ll and lh:
                swing_bear = True
                swing_desc = "波段LL+LH✓"
            else:
                swing_desc = "波段混乱"
        else:
            swing_desc = f"波段枢轴不足(h={len(highs)},l={len(lows)})"
    except (TypeError, ValueError, IndexError):
        pass

    pullback_atr = _max_pullback_atr(frame, window)
    shallow = None
    pullback_desc = "回撤:未知(ATR缺失)"
    if pullback_atr is not None:
        shallow = pullback_atr <= Prefill.ALWAYS_IN_PULLBACK_ATR
        pullback_desc = (
            f"最大区间{pullback_atr:.2f}×ATR"
            f"({'浅回撤✓' if shallow else '回撤较深✗'})"
        )

    bull_core = above >= same_side and slope_sign > 0
    bear_core = below >= same_side and slope_sign < 0
    gate3_bull = swing_bull and (shallow is None or shallow)
    gate3_bear = swing_bear and (shallow is None or shallow)

    return {
        "above": above,
        "below": below,
        "slope_sign": slope_sign,
        "slope_desc": slope_desc,
        "swing_desc": swing_desc,
        "pullback_desc": pullback_desc,
        "bull_core": bull_core,
        "bear_core": bear_core,
        "gate3_bull": gate3_bull,
        "gate3_bear": gate3_bear,
    }


def _max_pullback_atr(frame: KlineFrame, window: int) -> float | None:
    atr14 = frame.indicators.atr14
    if not atr14 or math.isnan(float(atr14[0])) or float(atr14[0]) <= 0:
        return None
    closes = [float(b.close) for b in list(frame.bars)[:window]]
    if len(closes) < 2:
        return None
    return (max(closes) - min(closes)) / float(atr14[0])
