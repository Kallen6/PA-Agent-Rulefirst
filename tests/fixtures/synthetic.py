"""8 类市场状态的确定性 K 线夹具（oldest-first dict rows）。

用途：离线验证闸门、灰区比例与端到端决策；不追求真实行情统计特征。

设计约束：
- 每种状态都由「可测量的几何关系」构造，便于断言闸门输出：
  尖峰 = 5 根低重叠趋势棒；微型通道 = 9 根带小回撤的连续推进；
  紧凑/常规/宽通道 = 回撤占腿高 25% / 40% / 65%，重叠率随宽度递增；
  趋势型区间 = 长横盘后两腿推进、末段深回撤；普通区间 = 箱体；
  极端区间 = 极窄高重叠横盘。
- 所有摆动转折点带额外影线（turn wick），保证 2 根分形枢轴可被确认。
"""
from __future__ import annotations

REGIMES = (
    "spike",
    "micro_channel",
    "tight_channel",
    "normal_channel",
    "broad_channel",
    "trending_tr",
    "trading_range",
    "extreme_tr",
)


def _bar(
    o: float,
    c: float,
    *,
    wick: float = 0.08,
    high_extra: float = 0.0,
    low_extra: float = 0.0,
    volume: float = 1000.0,
) -> dict:
    return {
        "ts_open": 0,
        "open": o,
        "high": max(o, c) + wick + high_extra,
        "low": min(o, c) - wick - low_extra,
        "close": c,
        "volume": volume,
    }


def _row(ts: int, o: float, c: float, wick: float = 0.08, volume: float = 1000.0) -> dict:
    row = _bar(o, c, wick=wick, volume=volume)
    row["ts_open"] = ts
    return row


def _finalize(rows: list[dict]) -> list[dict]:
    for i, row in enumerate(rows):
        row["ts_open"] = 1_000_000 + i * 60_000
    return rows


def _flat(
    n: int,
    price: float = 100.0,
    amp: float = 0.12,
    *,
    seed_offset: int = 0,
) -> list[dict]:
    """窄幅横盘：3 根一组的推进-回吐。

    影线取 1.6×振幅，使实体占比 <25%、收盘位置居中 → 不构成趋势棒，
    长程背景方向保持中性（否则平盘噪声会被五信号投票判成多/空）。
    """
    rows = []
    direction = 1
    for i in range(n):
        o = price
        c = o + amp * direction * (1 if i % 3 else -1)
        wick = amp * (1.60 + 0.05 * (i % 4)) + 0.001 * seed_offset
        rows.append(_bar(o, c, wick=wick))
        price = c
        if i % 4 == 3:
            direction *= -1
    return rows


def _leg(
    price: float,
    *,
    step: float,
    bars: int,
    wick_ratio: float,
    up: bool,
    turn_wick: float = 0.5,
) -> tuple[list[dict], float]:
    """一段单边推进；最后一根加长影线形成可确认的摆动极值。"""
    rows: list[dict] = []
    wick = abs(step) * wick_ratio
    for k in range(bars):
        o = price
        c = o + step
        extra = wick * turn_wick if k == bars - 1 else 0.0
        rows.append(
            _bar(
                o,
                c,
                wick=wick,
                high_extra=extra if up else 0.0,
                low_extra=extra if not up else 0.0,
            )
        )
        price = c
    return rows, price


def _zigzag(
    n: int,
    *,
    leg: float,
    pullback: float,
    bars_per_leg: int = 5,
    pullback_bars: int = 3,
    start: float = 100.0,
    wick_ratio: float = 0.15,
) -> list[dict]:
    """推进-回撤循环。

    先用 n % cycle 根补齐相位，再走完整周期，保证 n=100 时序列正好收在
    回撤末端（这样「最近一波回撤」才是可测量的，而不是停在新高途中）。
    """
    up_step = leg / bars_per_leg
    down_step = -pullback / pullback_bars
    rows: list[dict] = []
    price = start
    cycle = bars_per_leg + pullback_bars
    lead = n % cycle
    if lead:
        chunk, price = _leg(price, step=up_step, bars=lead, wick_ratio=wick_ratio, up=True)
        rows.extend(chunk)
    rising = True
    while len(rows) < n:
        step = up_step if rising else down_step
        bars = bars_per_leg if rising else pullback_bars
        chunk, price = _leg(
            price, step=step, bars=bars, wick_ratio=wick_ratio, up=rising
        )
        rows.extend(chunk)
        rising = not rising
    return rows


def _spike(n: int = 100) -> list[dict]:
    rows = _flat(n - 5, 100.0, amp=0.1)
    price = rows[-1]["close"]
    chunk, price = _leg(price, step=1.6, bars=5, wick_ratio=0.03, up=True)
    rows.extend(chunk)
    return rows


def _micro_channel(n: int = 100) -> list[dict]:
    """9 根连续推进：每根低开于前收 25%，形成小幅回撤但实体仍重叠。"""
    rows = _flat(n - 9, 100.0, amp=0.1)
    price = rows[-1]["close"]
    for _ in range(9):
        o = price - 0.25
        c = o + 0.5
        rows.append(_bar(o, c, wick=0.04))
        price = c
    return rows


def _trending_tr(n: int = 100) -> list[dict]:
    """长横盘后两腿推进，末段回撤至最后一腿的 60%。"""
    trend_bars = 20
    rows = _flat(n - trend_bars, 100.0, amp=0.10)
    price = rows[-1]["close"]
    up_step = 3.0 / 7
    down_step = -1.8 / 3
    for _ in range(2):
        chunk, price = _leg(price, step=up_step, bars=7, wick_ratio=0.2, up=True)
        rows.extend(chunk)
        chunk, price = _leg(price, step=down_step, bars=3, wick_ratio=0.2, up=False)
        rows.extend(chunk)
    return rows


def _trading_range(n: int = 100) -> list[dict]:
    rows = []
    price = 100.0
    lows, highs = 98.2, 101.8
    direction = 1
    for i in range(n):
        target = highs if direction > 0 else lows
        o = price
        c = o + max(-0.4, min(0.4, (target - o) / 6))
        # 影线随位置微变，并在转折棒加长，保证 2 根分形枢轴可被确认
        wick = 0.16 + 0.03 * (i % 5)
        turn = (direction > 0 and c >= highs - 0.15) or (direction < 0 and c <= lows + 0.15)
        rows.append(
            _bar(
                o,
                c,
                wick=wick,
                high_extra=0.06 if turn and direction > 0 else 0.0,
                low_extra=0.06 if turn and direction < 0 else 0.0,
            )
        )
        price = c
        if turn:
            direction *= -1
    return rows


def _extreme_tr(n: int = 100) -> list[dict]:
    rows = []
    price = 100.0
    for i in range(n):
        o = price
        up = i % 2 == 0
        c = o + (0.16 if up else -0.14)
        wick = 0.09 + 0.02 * (i % 3)
        rows.append(
            _bar(o, c, wick=wick, high_extra=0.03 if up else 0.0, low_extra=0.03 if not up else 0.0)
        )
        price = c
    return rows


def make_setup_rows(
    kind: str = "normal_channel", n: int = 100, direction: str = "bullish"
) -> list[dict]:
    """在状态夹具末端叠加一根合格信号棒（K2）+ 一根跟随棒（K1）。

    用途：端到端验证「有信号棒 → 三价合成 → §10/§11 通过 → 下单」的路径；
    信号棒带长影线（锤子/上影），实体不大，保证 §9.0/§9.3 可过。
    """
    rows = make_regime_rows(kind, n)
    anchor = rows[-3]["close"]
    if direction == "bullish":
        rows[-2] = _bar(anchor, anchor + 0.25, wick=0.03, low_extra=0.75)
        rows[-1] = _bar(anchor + 0.25, anchor + 0.65, wick=0.04)
    else:
        rows[-2] = _bar(anchor, anchor - 0.25, wick=0.03, high_extra=0.75)
        rows[-1] = _bar(anchor - 0.25, anchor - 0.65, wick=0.04)
    return _finalize(rows)


def make_regime_rows(kind: str, n: int = 100) -> list[dict]:
    if kind == "spike":
        rows = _spike(n)
    elif kind == "micro_channel":
        rows = _micro_channel(n)
    elif kind == "tight_channel":
        rows = _zigzag(n, leg=5.0, pullback=1.2, wick_ratio=0.10)
    elif kind == "normal_channel":
        rows = _zigzag(n, leg=5.0, pullback=1.9, wick_ratio=0.20)
    elif kind == "broad_channel":
        rows = _zigzag(n, leg=5.0, pullback=3.2, wick_ratio=0.45)
    elif kind == "trending_tr":
        rows = _trending_tr(n)
    elif kind == "trading_range":
        rows = _trading_range(n)
    elif kind == "extreme_tr":
        rows = _extreme_tr(n)
    else:
        raise ValueError(f"unknown regime: {kind}")
    return _finalize(rows[:n])
