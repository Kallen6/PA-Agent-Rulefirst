"""共享渲染层：决策报告 HTML 构建（零依赖、无 IO）。

从 `tools/export_html_report.py` 抽取，CLI 与 Web 决策台共用同一渲染路径：
- 输入：frame（鸭子协议：symbol/timeframe/bars/indicators）+ DecisionOutcome；
- 输出：整页 HTML 字符串（内联 CSS + SVG K 线，无外链资源）；
- 本模块不做任何网络/文件/浏览器 IO，可安全在 HTTP handler 中调用。

公开接口只有一个：`render_html(frame, outcome, *, source, title=None, generated_at=None)`。
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 运行期只做鸭子访问，避免潜在循环导入
    from pa_engine.engine.pipeline import DecisionOutcome

CHART_W = 1040
CHART_H = 470
PAD_L = 12
PAD_R = 86
PAD_T = 18
PAD_B = 30

PLAN_LINES = (
    ("entry_price", "入场", "#2563eb", "6 4"),
    ("stop_loss_price", "止损", "#dc2626", ""),
    ("take_profit_price", "TP1", "#16a34a", "6 4"),
    ("take_profit_price_2", "TP2", "#65a30d", "2 4"),
)

# ── 中文字段映射（只在渲染层转换，decision 原值保持英文以便回放/比对）──────
# 术语取自 Al Brooks 价格行为体系的中文译名，并与 A 股/加密行情的习惯叫法对齐。
ZH_TERMS: dict[str, str] = {
    # 周期定位（§2–§6 九类 cycle position）
    "spike": "尖峰",
    "micro_channel": "微型通道",
    "tight_channel": "紧凑通道",
    "normal_channel": "常规通道",
    "broad_channel": "宽通道",
    "trending_tr": "趋势型区间",
    "trading_range": "震荡区间",
    "extreme_tr": "极端混乱区间",
    "unknown": "未知",
    # 方向 / AlwaysIn
    "bullish": "多头",
    "bearish": "空头",
    "neutral": "中性",
    "none": "无",
    # 闸门与终态
    "proceed": "放行",
    "wait": "观望",
    "trade": "可交易",
    "reject": "否决",
    # 入场路径 / 止损依据
    "signal": "信号棒",
    "background_limit": "背景限价",
    "signal_bar": "信号棒极点",
    "structure": "结构失效位",
    # 尖峰阶段与三路径
    "ongoing": "进行中",
    "ended": "已结束",
    "flag": "旗形整理",
    "channel": "通道延续",
    "range": "区间整理",
    "sticky_reversal_risk": "粘性反转风险",
    # 区间位置（§6.3）
    "lower_boundary": "区间下沿",
    "upper_boundary": "区间上沿",
    "middle_third": "区间中部",
    # 突破质量（§6.5）
    "surviving": "突破站稳",
    "close_breakout": "收盘突破",
    "failed": "突破失败",
    "wick_probe": "长影试探",
    # 楔形类型（§8.2）
    "pullback": "回撤楔形",
    "reversal": "反转楔形",
    "parabolic": "抛物线楔形",
    # 信号棒评级（§9.0）
    "pass": "合格",
    "fail": "不合格",
    "weak": "偏弱",
    "borderline": "边界",
    # 结构位来源（§9.0P 背景限价的 candidates）
    "support": "支撑位",
    "resistance": "阻力位",
    "range_high": "区间高点",
    "range_low": "区间低点",
    "ema": "EMA20",
    # 路由依据（terminal.label 里括号内的分支名）
    "cycle": "周期定位",
    "zone": "区间位置",
    "breakout": "突破",
    "spike_path": "尖峰路径",
    "spike_stage": "尖峰阶段",
    "direction": "方向",
    "condition": "条件",
    # 判定来源
    "program": "代码",
    "llm": "模型",
    "n/a": "不适用",
    # 入场次序（§7.3）
    "first": "第一次",
    "second": "第二次",
    "third": "第三次",
    "second_or_third": "二次或三次",
}

# 诊断字典的键名映射（反转/楔形/入场候选等）
ZH_KEYS: dict[str, str] = {
    "trend": "趋势",
    "confirmed": "已确认",
    "trendline_broken": "趋势线破位",
    "failed_extreme_test": "极值测试失败",
    "single_bar": "单棒反转",
    "three_push": "三推",
    "direction": "方向",
    "type": "类型",
    "parabolic": "抛物线",
    "entry_style": "入场路径",
    "stop_reference": "止损依据",
    "sources": "结构位来源",
    "samples": "采样次数",
    "valid": "有效票数",
    "agree_ratio": "一致率",
    "disagreements": "分歧字段",
    "generated_at": "生成时间",
    "new_bars": "新增K线",
    "last_cycle_position": "上次周期定位",
    "last_gate_result": "上次闸门结论",
}


# ── 格式化 ──────────────────────────────────────────────────────────────────


def _zh(value: Any) -> str:
    """英文枚举 → 中文术语；未命中时原样返回（不改动数值与自由文本）。"""
    if isinstance(value, bool):
        return "是" if value else "否"
    if value is None:
        return "—"
    text = str(value).strip()
    return ZH_TERMS.get(text, ZH_TERMS.get(text.lower(), text))


def _dict_zh(payload: dict[str, Any]) -> str:
    """诊断字典 → `趋势=多头；已确认=否` 形式的中文摘要。"""
    parts = [
        f"{ZH_KEYS.get(str(key), str(key))}={_text(value)}"
        for key, value in payload.items()
    ]
    return "；".join(parts)


_ZH_PATTERN = re.compile(
    r"\b(" + "|".join(sorted((re.escape(k) for k in ZH_TERMS), key=len, reverse=True)) + r")\b"
)


def _zh_text(text: str) -> str:
    """自由文本里的英文枚举按词边界替换成中文，其余内容原样保留。

    `\\b` 把下划线视作单词字符，因此 `spike` 不会误伤 `spike_stage`，
    `range` 不会误伤 `trading_range`。
    """
    if not text:
        return text
    return _ZH_PATTERN.sub(lambda match: ZH_TERMS[match.group(1)], text)


_LIST_REPR = re.compile(r"\[\s*([^\[\]]*?)\s*\]")


def _clean_list_repr(text: str) -> str:
    """把 `['ema', 'support']` 这类 Python 列表 repr 摊平成 `EMA20 · 支撑位`。"""
    if "[" not in text:
        return text

    def repl(match: re.Match[str]) -> str:
        items = [part.strip().strip("'\"") for part in match.group(1).split(",")]
        return " · ".join(item for item in items if item)

    return _LIST_REPR.sub(repl, text)


def _px(value: Any) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return html.escape(str(value))
    magnitude = abs(number)
    if magnitude >= 1000:
        return f"{number:,.2f}"
    if magnitude >= 1:
        return f"{number:.4f}".rstrip("0").rstrip(".")
    if magnitude >= 0.01:
        return f"{number:.6f}".rstrip("0").rstrip(".")
    return f"{number:.8f}".rstrip("0").rstrip(".")


def _num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return html.escape(str(value))


def _pct(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return html.escape(str(value))


def _utc(ts_ms: Any) -> str:
    try:
        moment = datetime.fromtimestamp(float(ts_ms) / 1000, timezone.utc)
    except (TypeError, ValueError, OSError):
        return "—"
    return moment.strftime("%m-%d %H:%M")


def _utc_full(ts_ms: Any) -> str:
    try:
        moment = datetime.fromtimestamp(float(ts_ms) / 1000, timezone.utc)
    except (TypeError, ValueError, OSError):
        return "—"
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def _short_evidence(evidence: dict[str, Any], limit: int = 3) -> str:
    if not evidence:
        return "—"
    parts = []
    for key, value in list(evidence.items())[:limit]:
        if isinstance(value, float):
            text = f"{value:.4g}"
        elif isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False)
        else:
            text = _zh(value)
        if len(text) > 46:
            text = text[:43] + "…"
        parts.append(f"{key}={text}")
    if len(evidence) > limit:
        parts.append("…")
    return html.escape("; ".join(parts))


# ── 图表 ────────────────────────────────────────────────────────────────────


def _signal_index(decision: dict[str, Any], bar_count: int) -> int | None:
    """信号棒在时间序里的下标：seq=1 表示最新一根。"""
    for key in ("gate_trace", "decision_trace"):
        for item in decision.get(key) or []:
            if item.get("node_id") != "9.0":
                continue
            seq = (item.get("evidence") or {}).get("signal_bar_seq")
            if isinstance(seq, int) and 1 <= seq <= bar_count:
                return bar_count - seq
    basis = decision.get("entry_basis_bar")
    if isinstance(basis, str) and basis.startswith("K"):
        try:
            seq = int(basis[1:])
        except ValueError:
            return None
        if 1 <= seq <= bar_count:
            return bar_count - seq
    return None


def _render_chart(frame: Any, decision: dict[str, Any]) -> str:
    bars = list(reversed(frame.bars)) if frame is not None and frame.bars else []
    if not bars:
        return (
            '<div class="empty">该记录未保存 K 线数据，无法绘图。'
            "请用 <code>--live</code> 或 <code>--regime</code> 直接生成含图表的报告。</div>"
        )

    prices: list[float] = []
    for bar in bars:
        prices.extend([float(bar.high), float(bar.low)])
    for key, *_ in PLAN_LINES:
        value = decision.get(key)
        if value is not None:
            prices.append(float(value))
    lo, hi = min(prices), max(prices)
    if hi <= lo:
        hi = lo + (abs(lo) * 0.001 or 1.0)
    pad = (hi - lo) * 0.05
    lo, hi = lo - pad, hi + pad

    plot_w = CHART_W - PAD_L - PAD_R
    plot_h = CHART_H - PAD_T - PAD_B
    step = plot_w / max(len(bars), 1)
    body_w = max(1.5, step * 0.62)

    def x_of(index: int) -> float:
        return PAD_L + step * (index + 0.5)

    def y_of(price: float) -> float:
        return PAD_T + plot_h * (hi - float(price)) / (hi - lo)

    parts: list[str] = [
        f'<svg viewBox="0 0 {CHART_W} {CHART_H}" role="img" '
        f'aria-label="{html.escape(str(frame.symbol), quote=True)} K 线图">',
        f'<rect x="0" y="0" width="{CHART_W}" height="{CHART_H}" fill="#ffffff"/>',
    ]

    for tick in range(5):
        price = lo + (hi - lo) * tick / 4
        y = y_of(price)
        parts.append(
            f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{PAD_L + plot_w}" y2="{y:.1f}" '
            'stroke="#e5e7eb" stroke-width="0.7"/>'
        )
        parts.append(
            f'<text x="{PAD_L + plot_w + 8}" y="{y + 4:.1f}" fill="#6b7280" '
            f'font-size="10">{_px(price)}</text>'
        )

    ema = list(reversed(frame.indicators.ema20)) if getattr(frame, "indicators", None) else []
    if ema and len(ema) == len(bars):
        points = " ".join(
            f"{x_of(i):.1f},{y_of(v):.1f}"
            for i, v in enumerate(ema)
            if v is not None and float(v) == float(v)
        )
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="#f59e0b" '
            'stroke-width="1.3" opacity="0.85"/>'
        )

    signal_at = _signal_index(decision, len(bars))
    for index, bar in enumerate(bars):
        rising = float(bar.close) >= float(bar.open)
        color = "#16a34a" if rising else "#dc2626"
        cx = x_of(index)
        parts.append(
            f'<line x1="{cx:.1f}" y1="{y_of(bar.high):.1f}" x2="{cx:.1f}" '
            f'y2="{y_of(bar.low):.1f}" stroke="{color}" stroke-width="1"/>'
        )
        top = y_of(max(float(bar.open), float(bar.close)))
        bottom = y_of(min(float(bar.open), float(bar.close)))
        height = max(bottom - top, 1.0)
        parts.append(
            f'<rect x="{cx - body_w / 2:.1f}" y="{top:.1f}" width="{body_w:.1f}" '
            f'height="{height:.1f}" fill="{color}"/>'
        )
        if signal_at is not None and index == signal_at:
            parts.append(
                f'<rect x="{cx - step / 2:.1f}" y="{PAD_T}" width="{step:.1f}" '
                f'height="{plot_h}" fill="#facc15" opacity="0.14"/>'
            )
            parts.append(
                f'<rect x="{cx - body_w:.1f}" y="{top - 3:.1f}" '
                f'width="{body_w * 2:.1f}" height="{height + 6:.1f}" fill="none" '
                'stroke="#f59e0b" stroke-width="1.6" stroke-dasharray="3 2"/>'
            )

    plan_labels: list[dict[str, Any]] = []
    for key, label, color, dash in PLAN_LINES:
        value = decision.get(key)
        if value is None:
            continue
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        y = y_of(value)
        parts.append(
            f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{PAD_L + plot_w}" y2="{y:.1f}" '
            f'stroke="{color}" stroke-width="1.6"{dash_attr} opacity="0.92"/>'
        )
        plan_labels.append(
            {"y": y, "label": label, "color": color, "value": value}
        )

    # 价格接近时标签会互相压住：按 y 排序后逐级下推，保证至少 16px 间距
    plan_labels.sort(key=lambda item: item["y"])
    placed: list[float] = []
    for item in plan_labels:
        y = max(item["y"], PAD_T + 9)
        for _ in range(8):
            if all(abs(y - other) >= 16 for other in placed):
                break
            y += 16
        item["label_y"] = y
        placed.append(y)

    for item in plan_labels:
        y = item["label_y"]
        parts.append(
            f'<rect x="{PAD_L + 4}" y="{y - 9:.1f}" width="104" height="17" rx="3" '
            'fill="#ffffff" fill-opacity="0.9" stroke="#e2e8f0" stroke-width="0.6"/>'
        )
        parts.append(
            f'<text x="{PAD_L + 9}" y="{y + 4:.1f}" fill="{item["color"]}" '
            f'font-size="11" font-weight="600">{item["label"]} {_px(item["value"])}</text>'
        )

    for tick in range(5):
        index = round((len(bars) - 1) * tick / 4)
        anchor = "start" if tick == 0 else "end" if tick == 4 else "middle"
        x = x_of(index)
        if tick == 0:
            x = max(x, PAD_L)
        elif tick == 4:
            x = min(x, PAD_L + plot_w)
        parts.append(
            f'<text x="{x:.1f}" y="{CHART_H - 10}" text-anchor="{anchor}" '
            f'fill="#6b7280" font-size="10">{_utc(bars[index].ts_open)}</text>'
        )

    # 悬停命中列（透明覆盖整列，置于最上层）：携带该根 K 线完整数据供 tooltip 读取
    for index, bar in enumerate(bars):
        ema_val = ""
        if index < len(ema) and ema[index] is not None:
            candidate = float(ema[index])
            if candidate == candidate:  # 过滤 NaN（EMA 预热期）
                ema_val = f"{candidate:.6g}"
        rising = float(bar.close) >= float(bar.open)
        parts.append(
            f'<rect class="hit" x="{x_of(index) - step / 2:.1f}" y="{PAD_T}" '
            f'width="{step:.1f}" height="{plot_h}" '
            f'data-dir="{"up" if rising else "down"}" '
            f'data-o="{float(bar.open):.6g}" data-h="{float(bar.high):.6g}" '
            f'data-l="{float(bar.low):.6g}" data-c="{float(bar.close):.6g}" '
            f'data-v="{float(bar.volume or 0):.0f}" data-ema="{ema_val}" '
            f'data-t="{_utc_full(bar.ts_open)}"/>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


# ── 摘要与表格 ──────────────────────────────────────────────────────────────


def _badge(decision: dict[str, Any]) -> str:
    terminal = decision.get("terminal") or {}
    outcome = str(terminal.get("outcome") or "wait")
    label = {"trade": "可交易", "reject": "否决", "wait": "观望"}.get(outcome, _zh(outcome))
    return f'<span class="badge badge-{html.escape(outcome, quote=True)}">{html.escape(label)}</span>'


def _stance_text(decision: dict[str, Any]) -> str:
    """档位卡片：显示中文名与生效的 RR 门槛。"""
    stance = decision.get("stance") or {}
    if not isinstance(stance, dict) or not stance.get("label_zh"):
        return ""
    min_rr = stance.get("min_rr")
    min_gap = stance.get("min_equation_gap")
    rr_text = f"RR≥{min_rr:.1f}" if isinstance(min_rr, (int, float)) else ""
    gap_text = f"方程≥{min_gap:+.0%}" if isinstance(min_gap, (int, float)) else ""
    detail = " / ".join(part for part in (rr_text, gap_text) if part)
    return f"{stance['label_zh']}（{detail}）" if detail else str(stance["label_zh"])


def _cards(frame: Any, outcome: DecisionOutcome) -> str:
    decision = outcome.decision
    stage1 = outcome.stage1 or {}
    rr = "—"
    for result in outcome.gate_results:
        if result.node_id == "10.2":
            rr = _num((result.evidence or {}).get("rr"), 2)
            break
    items = [
        ("周期定位", decision.get("cycle_position")),
        ("下单倾向", _stance_text(decision)),
        ("方向", decision.get("direction")),
        ("在场方向", decision.get("always_in")),
        ("诊断置信", _pct(decision.get("diagnosis_confidence"))),
        ("闸门结论", decision.get("gate_result")),
        ("委托方式", decision.get("order_type")),
        ("入场路径", decision.get("entry_style")),
        ("入场价", _px(decision.get("entry_price"))),
        ("止损", _px(decision.get("stop_loss_price"))),
        ("TP1", _px(decision.get("take_profit_price"))),
        ("TP2", _px(decision.get("take_profit_price_2"))),
        ("RR", rr),
        ("估算胜率", _pct(decision.get("estimated_win_rate"))),
        ("尖峰阶段", stage1.get("spike_stage")),
        ("区间位置", stage1.get("zone")),
        ("突破质量", stage1.get("breakout_quality")),
    ]
    cells = []
    for label, value in items:
        if value is None or value == "":
            text = "—"
        else:
            text = html.escape(_text(value))
        cells.append(
            f'<div class="card"><div class="card-k">{html.escape(label)}</div>'
            f'<div class="card-v">{text}</div></div>'
        )
    return '<div class="cards">' + "".join(cells) + "</div>"


def _trace_table(title: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return f'<section class="panel"><h2>{html.escape(title)}</h2><p class="muted">无节点记录。</p></section>'
    body = []
    for row in rows:
        confidence = row.get("confidence")
        conf = f"{float(confidence) * 100:.0f}%" if isinstance(confidence, (int, float)) else "—"
        filled = str(row.get("filled_by") or "program")
        badge_class = "llm" if filled == "llm" else "prog"
        answer = str(row.get("answer"))
        answer_class = "yes" if answer in ("是", "True") else "no" if answer in ("否", "False") else "na"
        body.append(
            "<tr>"
            f'<td class="node">§{html.escape(str(row.get("node_id")))}</td>'
            f'<td>{html.escape(_zh_text(str(row.get("question") or "")))}</td>'
            f'<td><span class="ans ans-{answer_class}">{html.escape(_zh(answer))}</span></td>'
            f'<td class="num">{conf}</td>'
            f'<td class="evidence">{_short_evidence(row.get("evidence") or {})}</td>'
            f'<td><span class="src src-{badge_class}">{html.escape(_zh(filled))}</span></td>'
            f'<td class="reason">{html.escape(_zh_text(str(row.get("reason") or "")))}</td>'
            "</tr>"
        )
    return (
        f'<section class="panel"><h2>{html.escape(title)}</h2>'
        '<table><thead><tr><th>节点</th><th>问题</th><th>判定</th><th>置信</th>'
        "<th>关键证据</th><th>来源</th><th>依据</th></tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></section>"
    )


def _kv(pairs: list[tuple[str, Any]]) -> str:
    rows = "".join(
        f"<tr><th>{html.escape(label)}</th><td>{html.escape(_text(value))}</td></tr>"
        for label, value in pairs
    )
    return f'<table class="kv">{rows}</table>'


def _text(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, dict):
        return _dict_zh(value) if value else "—"
    if isinstance(value, (list, tuple)):
        return " · ".join(_text(item) for item in value) if value else "—"
    return _clean_list_repr(_zh_text(_zh(value)))


def _gray_panel(outcome: DecisionOutcome) -> str:
    decision = outcome.decision
    gray = outcome.gray_zone
    stats = outcome.prompt_stats or {}
    rows = [
        ("need_llm", gray.need_llm if gray else False),
        ("contested", list(gray.contested_nodes) if gray else []),
        ("触发原因", list(gray.reasons) if gray else []),
        ("实际调用", outcome.llm_called),
        ("prompt 字节", stats.get("bytes", 0)),
        ("估算 tokens", stats.get("estimated_tokens")),
        ("prompt sha256", (stats.get("sha256") or "")[:16]),
    ]
    extra = ""
    if decision.get("vote"):
        extra += "<h3>自一致性投票</h3>" + _kv(
            [(k, decision["vote"].get(k)) for k in ("samples", "valid", "agree_ratio", "disagreements")]
        )
    if decision.get("incremental"):
        extra += "<h3>增量锚定</h3>" + _kv(
            [(k, decision["incremental"].get(k)) for k in ("generated_at", "new_bars", "last_cycle_position", "last_gate_result")]
        )
    if decision.get("llm_error"):
        extra += f'<p class="error">感知层错误：{html.escape(str(decision["llm_error"]))}</p>'
    answer = outcome.llm_answer
    if answer:
        extra += (
            '<h3>感知层答复</h3><pre class="json">'
            + html.escape(json.dumps(answer, ensure_ascii=False, indent=2))
            + "</pre>"
        )
    return (
        '<section class="panel"><h2>灰区与感知层</h2>'
        + _kv(rows)
        + extra
        + "</section>"
    )


def _meta_strip(frame: Any, source: str) -> str:
    symbol = getattr(frame, "symbol", None) or "—"
    timeframe = getattr(frame, "timeframe", None) or "—"
    bars = getattr(frame, "bars", ()) or ()
    newest = bars[0] if bars else None
    if newest is None:
        stamp = "—"
    elif float(newest.ts_open) < 946_684_800_000:  # 2000-01-01 前视作合成时间轴
        stamp = "合成时间轴"
    else:
        stamp = _utc_full(newest.ts_open)
    parts = [
        f"<span><b>{html.escape(str(symbol))}</b> {html.escape(str(timeframe))}</span>",
        f"<span>K 线 {len(bars)} 根</span>",
        f"<span>最新 {html.escape(stamp)}</span>",
        f"<span>来源 {html.escape(source)}</span>",
    ]
    return '<div class="meta">' + "".join(parts) + "</div>"


CSS = """
*{box-sizing:border-box}
body{margin:0;padding:26px;background:#f1f5f9;color:#0f172a;
  font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:20px;margin:0 0 6px}
h2{font-size:14px;margin:0 0 12px;color:#334155;letter-spacing:.02em}
h3{font-size:13px;margin:16px 0 6px;color:#475569}
.meta{display:flex;flex-wrap:wrap;gap:16px;color:#64748b;font-size:12px;margin-bottom:14px}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600;margin-left:8px}
.badge-trade{background:#dcfce7;color:#15803d}
.badge-reject{background:#ffedd5;color:#c2410c}
.badge-wait{background:#e2e8f0;color:#475569}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(132px,1fr));gap:10px;margin:16px 0}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:9px 11px}
.card-k{font-size:11px;color:#94a3b8}
.card-v{font-size:15px;font-weight:600;margin-top:2px;word-break:break-all}
.panel{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:16px;margin:16px 0;overflow-x:auto}
.chart{padding:10px;background:#fff;border:1px solid #e2e8f0;border-radius:12px;margin:16px 0}
.chart svg{width:100%;height:auto;display:block}
.hit{fill:transparent;pointer-events:all}
.hit:hover{fill:rgba(100,116,139,0.10)}
#kbar-tip{position:fixed;z-index:99;pointer-events:none;display:none;
  background:rgba(15,23,42,0.94);color:#e2e8f0;border:1px solid #334155;border-radius:8px;
  padding:8px 11px;font:11.5px/1.65 ui-monospace,Consolas,monospace;
  box-shadow:0 8px 24px rgba(0,0,0,.35);min-width:172px}
#kbar-tip b{color:#facc15;font-weight:600;display:block;margin-bottom:3px}
#kbar-tip .r{display:flex;justify-content:space-between;gap:16px}
#kbar-tip .r span:first-child{color:#94a3b8}
#kbar-tip .up{color:#4ade80}
#kbar-tip .down{color:#f87171}
.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:12px;color:#64748b;padding:6px 4px 0}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:middle}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #eef2f7;vertical-align:top}
thead th{color:#64748b;font-weight:600;background:#f8fafc;position:sticky;top:0}
th:first-child{white-space:nowrap}
td.num{text-align:right;white-space:nowrap;color:#475569}
td.node{white-space:nowrap;color:#475569;font-weight:600}
td.evidence{color:#64748b;font-family:ui-monospace,Consolas,monospace;font-size:11.5px}
td.reason{color:#475569}
.ans{padding:1px 7px;border-radius:5px;font-size:11.5px;font-weight:600}
.ans-yes{background:#dcfce7;color:#15803d}
.ans-no{background:#fee2e2;color:#b91c1c}
.ans-na{background:#e2e8f0;color:#475569}
.src{font-size:11px;padding:1px 6px;border-radius:5px}
.src-prog{background:#e0f2fe;color:#0369a1}
.src-llm{background:#fae8ff;color:#a21caf}
table.kv th{width:190px;color:#64748b;font-weight:500;background:#f8fafc}
pre.json{background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;
  overflow:auto;max-height:420px;font-size:11.5px;line-height:1.5}
.muted{color:#94a3b8}
.empty{background:#fff7ed;border:1px dashed #fdba74;color:#9a3412;
  padding:28px;border-radius:12px;text-align:center}
.error{background:#fef2f2;border:1px solid #fecaca;color:#b91c1c;padding:8px 10px;border-radius:8px}
details{margin:14px 0}
summary{cursor:pointer;color:#334155;font-weight:600}
footer{color:#94a3b8;font-size:11.5px;margin:22px 0 8px;text-align:center}
@media print{body{background:#fff;padding:0}.panel,.chart{break-inside:avoid}}
"""


def render_html(
    frame: Any,
    outcome: DecisionOutcome,
    *,
    source: str,
    title: str | None = None,
    generated_at: str | None = None,
) -> str:
    decision = outcome.decision
    symbol = getattr(frame, "symbol", None) or "PA"
    timeframe = getattr(frame, "timeframe", None) or ""
    heading = title or f"{symbol} {timeframe} 决策报告"
    terminal = decision.get("terminal") or {}
    outcome_zh = _zh(terminal.get("outcome") or "wait")
    stamp = generated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    returns = [
        ("终态", f"§{terminal.get('node_id') or '—'} {outcome_zh}"),
        ("终态说明", terminal.get("label") or decision.get("reason")),
        ("形态识别", decision.get("patterns")),
        ("反转诊断", decision.get("reversal")),
        ("楔形诊断", decision.get("wedge")),
        ("入场规则", decision.get("entry_rule")),
        ("止损依据", decision.get("stop_reference")),
    ]
    legend = (
        '<div class="legend">'
        '<span><i style="background:#16a34a"></i>阳线</span>'
        '<span><i style="background:#dc2626"></i>阴线</span>'
        '<span><i style="background:#f59e0b"></i>EMA20</span>'
        '<span><i style="background:#facc15"></i>信号棒</span>'
        '<span><i style="background:#2563eb"></i>入场</span>'
        '<span><i style="background:#dc2626"></i>止损</span>'
        '<span><i style="background:#16a34a"></i>目标</span>'
        "</div>"
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(heading)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>{html.escape(heading)}{_badge(decision)}</h1>
  {_meta_strip(frame, source)}
  {_cards(frame, outcome)}
  <div class="chart">{_render_chart(frame, decision)}{legend}</div>
  <section class="panel"><h2>结论与形态</h2>{_kv(returns)}</section>
  {_trace_table("阶段一：结构与状态", decision.get("gate_trace") or [])}
  {_trace_table("阶段二：入场、风险、下单", decision.get("decision_trace") or [])}
  {_gray_panel(outcome)}
  <details><summary>原始 decision JSON</summary>
    <pre class="json">{html.escape(json.dumps(decision, ensure_ascii=False, indent=2, default=str))}</pre>
  </details>
  <details><summary>完整闸门证据（gate_evidence）</summary>
    <pre class="json">{html.escape(json.dumps([r.to_trace() for r in outcome.gate_results], ensure_ascii=False, indent=2, default=str))}</pre>
  </details>
  <footer>re_pa_agent · 单文件离线报告 · 生成于 {stamp}</footer>
</div>
<div id="kbar-tip"></div>
<script>
(function () {{
  var chart = document.querySelector('.chart svg');
  var tip = document.getElementById('kbar-tip');
  if (!chart || !tip) return;
  var rows = [['o', '开'], ['h', '高'], ['l', '低'], ['c', '收'], ['v', '量'], ['ema', 'EMA20']];
  function fmt(v) {{ return v === '' ? '—' : v; }}
  chart.addEventListener('mousemove', function (ev) {{
    var hit = ev.target && ev.target.closest ? ev.target.closest('.hit') : null;
    if (!hit) {{ tip.style.display = 'none'; return; }}
    var dir = hit.getAttribute('data-dir') === 'up' ? 'up' : 'down';
    var html = '<b>' + hit.getAttribute('data-t') + '</b>';
    rows.forEach(function (pair) {{
      var value = fmt(hit.getAttribute('data-' + pair[0]));
      var cls = pair[0] === 'c' ? ' class="' + dir + '"' : '';
      html += '<div class="r"><span>' + pair[1] + '</span><span' + cls + '>' + value + '</span></div>';
    }});
    tip.innerHTML = html;
    tip.style.display = 'block';
    var x = ev.clientX + 14, y = ev.clientY + 14;
    if (x + tip.offsetWidth > window.innerWidth - 8) x = ev.clientX - tip.offsetWidth - 14;
    if (y + tip.offsetHeight > window.innerHeight - 8) y = ev.clientY - tip.offsetHeight - 14;
    tip.style.left = x + 'px';
    tip.style.top = y + 'px';
  }});
  chart.addEventListener('mouseleave', function () {{ tip.style.display = 'none'; }});
}})();
</script>
</body>
</html>
"""
