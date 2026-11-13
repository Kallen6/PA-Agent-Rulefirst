"""入场信号闸门（§9，A 类为主 + §9.0/§9.0P 双路径）。

节点映射（二元决策.txt §9）：
    §9.0  信号棒质量       代码打分；清晰过/清晰不过直接判，边界（2 分）交灰区 LLM
    §9.0P 背景驱动限价     §9.0=否时的正式第二路径（结构位/边界/EMA）
    §9.1  信号K线已收盘    恒定节点（constant.py）
    §9.2  信号棒方向一致   纯枚举比较
    §9.3  信号棒是否过长   信号棒振幅 ≥1.5×ATR
    §9.4  第一次入场       H1/H2、L1/L2 计数
    §9.5  是否有跟随       复用 features/kline.py 的 follow_through_1_2
    §9.6  二次入场         计数 + 价格更优陷阱警告
    §9.7  入场棒强度       计划型限价记 N/A，不否决方案

信号棒口径：`bars[1]`（K2）。K1 是最新已收盘棒，突破单以 K2 极点 ±1 跳为依据。
"""
from __future__ import annotations

from pa_engine.config.thresholds import Entry
from pa_engine.features.kline import bar_candle_direction_label
from pa_engine.gates.base import (
    GateContext,
    GateResult,
    FrameLike,
    atr_of,
    ema_of,
    kline_features_of,
    register,
    structure_of,
)

_CHANNELS = ("micro_channel", "tight_channel", "normal_channel", "broad_channel")
_RANGES = ("trading_range", "trending_tr")
_SPIKE = ("spike",)


def na_result(node_id: str, reason: str, evidence: dict | None = None) -> GateResult:
    """不适用节点：答案写 n/a，避免被当作「否」阻断链路。"""
    return GateResult(
        node_id=node_id,
        answer="n/a",
        branch="n/a",
        confidence=0.9,
        evidence=evidence or {},
        reason=reason,
    )


def signal_bar_index(frame: FrameLike, ctx: GateContext) -> int:
    """信号棒在 bars 中的下标（默认 K2；数据不足时退化为 K1）。"""
    bars = getattr(frame, "bars", ()) or ()
    return 1 if len(bars) >= 2 else 0


def _planned_direction(ctx: GateContext) -> str:
    direction = ctx.extras.get("direction")
    if direction in ("bullish", "bearish"):
        return direction
    return "neutral"


@register("9.0")
def gate_9_0(frame: FrameLike, ctx: GateContext) -> GateResult:
    """信号棒质量三分诊：得分 ≥3 pass、≤1 fail、=2 borderline（交灰区）。"""
    bars = getattr(frame, "bars", ()) or ()
    if not bars:
        return GateResult("9.0", False, "no_data", 0.9, {}, "无K线，无法评估信号棒")
    idx = signal_bar_index(frame, ctx)
    feature = kline_features_of(frame, ctx)[idx]
    direction = _planned_direction(ctx)
    factors: dict[str, bool] = {}

    body = feature.body_ratio if feature.body_ratio is not None else 0.0
    factors["body"] = body >= Entry.SIGNAL_BAR_BODY_MIN
    close_pos = feature.close_position
    if direction == "bullish":
        factors["close_position"] = close_pos is not None and close_pos >= Entry.SIGNAL_BAR_CLOSE_POS_BULL
    elif direction == "bearish":
        factors["close_position"] = close_pos is not None and close_pos <= Entry.SIGNAL_BAR_CLOSE_POS_BEAR
    else:
        factors["close_position"] = False
    label = bar_candle_direction_label(bars[idx])
    factors["direction"] = (
        (direction == "bullish" and label == "阳线")
        or (direction == "bearish" and label == "阴线")
    )
    range_atr = feature.range_atr_ratio
    factors["size"] = range_atr is not None and range_atr <= Entry.SIGNAL_BAR_LONG_ATR

    score = sum(1 for ok in factors.values() if ok)
    cycle = str(ctx.extras.get("cycle_position") or "")
    from pa_engine.engine.stance import stance_of

    policy = stance_of(ctx)
    pass_score = policy.signal_pass_score
    fail_score = policy.signal_fail_score
    if cycle == "trading_range":
        sf = structure_of(frame, ctx)
        if sf.zone == "middle_third":
            # 中部强制压线：无论档位一律不合格（各档 fail_score ≥ 0）
            score = min(score, fail_score)

    if score >= pass_score:
        answer, branch, conf = True, "pass", 0.85
    elif score <= fail_score:
        answer, branch, conf = False, "fail", 0.85
    else:
        answer, branch, conf = False, "borderline", 0.55

    label_text = {"pass": "合格", "fail": "不合格", "borderline": "边界"}[branch]
    return GateResult(
        node_id="9.0",
        answer=answer,
        branch=branch,
        confidence=conf,
        evidence={
            "signal_bar_seq": int(bars[idx].seq),
            "score": score,
            "factors": factors,
            "body_ratio": body,
            "close_position": close_pos,
            "range_atr": range_atr,
            "candle": label,
            "cycle_position": cycle or None,
            "pass_score": pass_score,
            "fail_score": fail_score,
            "stance": policy.name,
        },
        reason=(
            f"信号棒K{int(bars[idx].seq)}质量{score}/4（{_factor_text(factors)}）→{label_text}"
            f"（{policy.label_zh}档：≥{pass_score}合格、≤{fail_score}不合格）"
        ),
    )


def _factor_text(factors: dict[str, bool]) -> str:
    return "、".join(f"{name}{'+' if ok else '-'}" for name, ok in factors.items())


@register("9.2")
def gate_9_2(frame: FrameLike, ctx: GateContext) -> GateResult:
    bars = getattr(frame, "bars", ()) or ()
    direction = _planned_direction(ctx)
    if not bars or direction == "neutral":
        return GateResult(
            "9.2", False, "no_direction", 0.8, {"direction": direction}, "计划方向未定，方向一致性不成立"
        )
    idx = signal_bar_index(frame, ctx)
    label = bar_candle_direction_label(bars[idx])
    aligned = (direction == "bullish" and label == "阳线") or (
        direction == "bearish" and label == "阴线"
    )
    branch = "aligned" if aligned else ("doji" if label == "平" else "opposite")
    return GateResult(
        node_id="9.2",
        answer=aligned,
        branch=branch,
        confidence=0.9 if label != "平" else 0.75,
        evidence={"signal_bar_seq": int(bars[idx].seq), "candle": label, "direction": direction},
        reason=f"信号棒{label} vs 计划{direction} → {'一致' if aligned else '不一致'}",
    )


@register("9.3")
def gate_9_3(frame: FrameLike, ctx: GateContext) -> GateResult:
    bars = getattr(frame, "bars", ()) or ()
    if not bars:
        return GateResult("9.3", False, "no_data", 0.9, {}, "无K线，无法评估信号棒长度")
    idx = signal_bar_index(frame, ctx)
    feature = kline_features_of(frame, ctx)[idx]
    ratio = feature.range_atr_ratio
    if ratio is None:
        return GateResult(
            "9.3", False, "unknown", 0.55, {"range_atr": None}, "ATR缺失，信号棒长度进入灰区"
        )
    too_long = ratio >= Entry.SIGNAL_BAR_LONG_ATR
    near = abs(ratio - Entry.SIGNAL_BAR_LONG_ATR) <= Entry.SIGNAL_BAR_LONG_ATR * 0.15
    return GateResult(
        node_id="9.3",
        answer=too_long,
        branch="too_long" if too_long else "ok",
        confidence=0.6 if near else 0.85,
        evidence={
            "signal_bar_seq": int(bars[idx].seq),
            "range_atr": ratio,
            "threshold_atr": Entry.SIGNAL_BAR_LONG_ATR,
        },
        reason=(
            f"信号棒振幅{ratio:.2f}×ATR"
            f"（{'≥' if too_long else '<'}{Entry.SIGNAL_BAR_LONG_ATR}×）"
            f"→{'过长' if too_long else '可接受'}"
        ),
    )


@register("9.5")
def gate_9_5(frame: FrameLike, ctx: GateContext) -> GateResult:
    bars = getattr(frame, "bars", ()) or ()
    if len(bars) < 2:
        return GateResult("9.5", False, "pending", 0.6, {}, "无后续K线，跟随待确认")
    idx = signal_bar_index(frame, ctx)
    feature = kline_features_of(frame, ctx)[idx]
    state = feature.follow_through_1_2
    answer = state == "yes"
    branch = state if state in ("yes", "failed", "no", "pending") else "unknown"
    conf = 0.85 if state in ("yes", "failed") else 0.6
    return GateResult(
        node_id="9.5",
        answer=answer,
        branch=branch,
        confidence=conf,
        evidence={"signal_bar_seq": int(bars[idx].seq), "follow_through": state},
        reason=f"信号棒跟随状态={state} → {'有跟随' if answer else '无/待确认'}",
    )


def _entry_candidates(
    frame: FrameLike, ctx: GateContext, direction: str
) -> tuple[float | None, float | None, float | None, list[float]]:
    """返回 (限价入场候选, 结构失效位, 目标候选, 候选来源列表)。"""
    sf = structure_of(frame, ctx)
    bars = getattr(frame, "bars", ()) or ()
    if not bars:
        return None, None, None, []
    close = float(bars[0].close)
    atr = atr_of(frame, ctx) or 0.0
    eps = max(atr * 1e-6, 1e-9)
    cycle = str(ctx.extras.get("cycle_position") or "")
    ema = ema_of(frame, ctx)
    sources: list[str] = []

    if direction == "bullish":
        invalidation = sf.invalidation_long
        floor = (invalidation + eps) if invalidation is not None else None
        levels = [
            p for p in sf.supports if p < close - eps and (floor is None or p > floor)
        ]
        sources.extend(["support"] * len(levels))
        if (
            cycle in _RANGES
            and sf.range_low is not None
            and sf.range_low < close - eps
            and (floor is None or sf.range_low > floor)
        ):
            levels.append(float(sf.range_low))
            sources.append("range_low")
        if ema is not None and ema < close - eps and (floor is None or ema > floor):
            levels.append(float(ema))
            sources.append("ema")
        if not levels:
            return None, invalidation, None, sources
        entry = max(levels)
        targets = [p for p in sf.resistances if p > entry + eps]
        if cycle in _RANGES and sf.range_high is not None and sf.range_high > entry + eps:
            targets.append(float(sf.range_high))
        targets.extend(m.target_price for m in sf.measured_moves if m.target_price > entry + eps)
        return entry, invalidation, (min(targets) if targets else None), sources

    if direction == "bearish":
        invalidation = sf.invalidation_short
        ceiling = (invalidation - eps) if invalidation is not None else None
        levels = [
            p for p in sf.resistances if p > close + eps and (ceiling is None or p < ceiling)
        ]
        sources.extend(["resistance"] * len(levels))
        if (
            cycle in _RANGES
            and sf.range_high is not None
            and sf.range_high > close + eps
            and (ceiling is None or sf.range_high < ceiling)
        ):
            levels.append(float(sf.range_high))
            sources.append("range_high")
        if ema is not None and ema > close + eps and (ceiling is None or ema < ceiling):
            levels.append(float(ema))
            sources.append("ema")
        if not levels:
            return None, invalidation, None, sources
        entry = min(levels)
        targets = [p for p in sf.supports if p < entry - eps]
        if cycle in _RANGES and sf.range_low is not None and sf.range_low < entry - eps:
            targets.append(float(sf.range_low))
        targets.extend(m.target_price for m in sf.measured_moves if m.target_price < entry - eps)
        return entry, invalidation, (max(targets) if targets else None), sources

    return None, None, None, sources


@register("9.0P")
def gate_9_0p(frame: FrameLike, ctx: GateContext) -> GateResult:
    """§9.0=否 的正式第二路径：背景驱动限价单可行性。"""
    cycle = str(ctx.extras.get("cycle_position") or "unknown")
    direction = _planned_direction(ctx)
    always_in = str(ctx.extras.get("always_in") or "none")
    sf = structure_of(frame, ctx)
    bars = getattr(frame, "bars", ()) or ()
    atr = atr_of(frame, ctx) or 0.0

    evidence = {
        "cycle_position": cycle,
        "direction": direction,
        "always_in": always_in,
        "zone": sf.zone,
        "barbwire": sf.barbwire_candidate,
    }

    if cycle not in _CHANNELS + _RANGES + _SPIKE:
        return GateResult("9.0P", False, "inapplicable", 0.85, evidence, f"周期{cycle}不支持背景限价")
    if direction not in ("bullish", "bearish"):
        return GateResult("9.0P", False, "no_direction", 0.85, evidence, "方向中性，禁止双边限价")
    if always_in in ("bullish", "bearish") and always_in != direction:
        evidence["side_conflict"] = True
        return GateResult(
            "9.0P", False, "side_conflict", 0.85, evidence, "限价方向与 Always In 相反，禁止逆势"
        )
    if sf.barbwire_candidate:
        return GateResult("9.0P", False, "barbwire", 0.85, evidence, "铁丝网无边界锚点，不挂单")
    if cycle == "trading_range" and sf.zone == "middle_third":
        return GateResult("9.0P", False, "middle_third", 0.85, evidence, "区间中部不挂计划型限价")
    if atr <= 0 or not bars:
        return GateResult("9.0P", False, "no_atr", 0.6, evidence, "ATR缺失，无法度量边界距离")

    entry, stop, target, sources = _entry_candidates(frame, ctx, direction)
    close = float(bars[0].close)
    evidence.update(
        {
            "entry_candidate": entry,
            "stop_candidate": stop,
            "target_candidate": target,
            "sources": sources,
        }
    )
    if entry is None or stop is None or target is None:
        return GateResult("9.0P", False, "no_structure", 0.85, evidence, "缺少结构位/失效位/目标，不能挂限价")
    distance = abs(close - entry)
    evidence["distance_atr"] = round(distance / atr, 4)
    if direction == "bullish":
        if not entry < close:
            return GateResult("9.0P", False, "wrong_side", 0.85, evidence, "做多限价须低于K1收盘")
        if not stop < entry:
            return GateResult("9.0P", False, "bad_stop", 0.85, evidence, "结构失效位须低于限价入场")
    else:
        if not entry > close:
            return GateResult("9.0P", False, "wrong_side", 0.85, evidence, "做空限价须高于K1收盘")
        if not stop > entry:
            return GateResult("9.0P", False, "bad_stop", 0.85, evidence, "结构失效位须高于限价入场")
    # 档位只放宽「距离边界」：乘数作用于 ATR 距离区间，其余硬禁令不变
    from pa_engine.engine.stance import stance_of

    policy = stance_of(ctx)
    min_atr = Entry.BACKGROUND_LIMIT_MIN_ATR * policy.bg_limit_min_mult
    max_atr = Entry.BACKGROUND_LIMIT_MAX_ATR * policy.bg_limit_max_mult
    evidence["bg_limit_min_atr"] = round(min_atr, 4)
    evidence["bg_limit_max_atr"] = round(max_atr, 4)
    evidence["stance"] = policy.name
    if distance < min_atr * atr:
        return GateResult(
            "9.0P", False, "too_close", 0.85, evidence, "结构位贴现价，等同市价追单"
        )
    if distance > max_atr * atr:
        return GateResult("9.0P", False, "too_far", 0.85, evidence, "结构位距现价过远，不具备可执行性")
    if direction == "bullish" and float(bars[0].low) <= stop:
        return GateResult("9.0P", False, "stop_hit", 0.85, evidence, "K1已触及止损，方案失效")
    if direction == "bearish" and float(bars[0].high) >= stop:
        return GateResult("9.0P", False, "stop_hit", 0.85, evidence, "K1已触及止损，方案失效")

    confidence = 0.8 if distance <= max_atr * atr * 0.75 else 0.65
    ctx.extras["background_limit"] = {
        "entry": entry,
        "stop": stop,
        "target": target,
        "direction": direction,
        "sources": sources,
        "distance_atr": round(distance / atr, 4),
    }
    return GateResult(
        node_id="9.0P",
        answer=True,
        branch="background_limit",
        confidence=confidence,
        evidence=evidence,
        reason=(
            f"{cycle}/{direction} 顺 Always In 一侧，结构位{entry:.6g}"
            f"（距{evidence['distance_atr']}ATR），失效位{stop:.6g}，目标{target:.6g}"
        ),
    )


@register("9.4")
def gate_9_4(frame: FrameLike, ctx: GateContext) -> GateResult:
    direction = _planned_direction(ctx)
    if direction == "neutral":
        return na_result("9.4", "方向未定，入场次数不适用")
    sf = structure_of(frame, ctx)
    count = sf.hl_count.bull_count if direction == "bullish" else sf.hl_count.bear_count
    first = count <= Entry.FIRST_ENTRY_MAX_HL
    branch = "first" if first else ("second" if count < Entry.THIRD_ENTRY_MIN_HL else "third+")
    return GateResult(
        node_id="9.4",
        answer=first,
        branch=branch,
        confidence=0.8,
        evidence={
            "direction": direction,
            "hl_count": count,
            "candidate": (
                sf.hl_count.bull_candidate if direction == "bullish" else sf.hl_count.bear_candidate
            ),
        },
        reason=f"{direction} H/L计数={count} → {'第一次入场' if first else branch}",
    )


@register("9.6")
def gate_9_6(frame: FrameLike, ctx: GateContext) -> GateResult:
    direction = _planned_direction(ctx)
    if direction == "neutral":
        return na_result("9.6", "方向未定，二次入场不适用")
    sf = structure_of(frame, ctx)
    bars = getattr(frame, "bars", ()) or ()
    atr = atr_of(frame, ctx) or 0.0
    count = sf.hl_count.bull_count if direction == "bullish" else sf.hl_count.bear_count
    second = count >= Entry.SECOND_ENTRY_MIN_HL
    trigger_seq = (
        sf.hl_count.last_bull_trigger_seq
        if direction == "bullish"
        else sf.hl_count.last_bear_trigger_seq
    )
    trap = False
    first_price = None
    if second and trigger_seq is not None and bars:
        trigger = next((b for b in bars if int(b.seq) == int(trigger_seq)), None)
        if trigger is not None:
            first_price = float(trigger.high) if direction == "bullish" else float(trigger.low)
            close = float(bars[0].close)
            if direction == "bullish":
                trap = close <= first_price - Entry.SECOND_ENTRY_TRAP_ATR * atr
            else:
                trap = close >= first_price + Entry.SECOND_ENTRY_TRAP_ATR * atr
    branch = "second" if second else "first"
    if count >= Entry.THIRD_ENTRY_MIN_HL:
        branch = "third+"
    return GateResult(
        node_id="9.6",
        answer=second,
        branch=branch,
        confidence=0.6 if trap else 0.75,
        evidence={
            "direction": direction,
            "hl_count": count,
            "first_entry_price": first_price,
            "better_price_trap": trap,
        },
        reason=(
            f"{direction} 第{count}次尝试 → {'二次入场' if second else '尚无二次入场'}"
            + ("；价格明显优于第一次=陷阱警告" if trap else "")
        ),
    )


@register("9.7")
def gate_9_7(frame: FrameLike, ctx: GateContext) -> GateResult:
    entry_style = str(ctx.extras.get("entry_style") or "signal")
    if entry_style != "signal":
        return na_result("9.7", "计划型限价尚无入场棒，记 N/A，不否决方案")
    bars = getattr(frame, "bars", ()) or ()
    direction = _planned_direction(ctx)
    if not bars or direction == "neutral":
        return na_result("9.7", "无入场棒或方向未定")
    feature = kline_features_of(frame, ctx)[0]
    label = bar_candle_direction_label(bars[0])
    body = feature.body_ratio or 0.0
    close_pos = feature.close_position
    aligned = (direction == "bullish" and label == "阳线") or (
        direction == "bearish" and label == "阴线"
    )
    if direction == "bullish":
        pos_ok = close_pos is not None and close_pos >= Entry.ENTRY_BAR_CLOSE_POS_BULL
    else:
        pos_ok = close_pos is not None and close_pos <= Entry.ENTRY_BAR_CLOSE_POS_BEAR
    strong = bool(aligned and body >= Entry.ENTRY_BAR_BODY_MIN and pos_ok)
    return GateResult(
        node_id="9.7",
        answer=strong,
        branch="strong" if strong else "weak",
        confidence=0.75,
        evidence={
            "entry_bar_seq": int(bars[0].seq),
            "candle": label,
            "body_ratio": body,
            "close_position": close_pos,
        },
        reason=f"入场棒{label}实体{body:.0%}收盘位置{close_pos} → {'强势' if strong else '偏弱'}",
    )
