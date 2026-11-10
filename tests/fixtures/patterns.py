"""结构性行情夹具：折线路径 → K 线行（oldest-first）。

用于反转 / 楔形 / 三角形 / 二次入场等 B 类节点的确定性单测。
每个拐点最后一根 K 线带 0.5 的极点影线，保证 2 根确认的摆动点可被判出。
"""
from __future__ import annotations

from typing import Iterable


def zigzag_rows(
    points: Iterable[float],
    *,
    bars_per_leg: int = 5,
    volume: float = 1000.0,
    ts_start: int = 2_000_000,
    tail: int = 3,
) -> list[dict]:
    pts = [float(p) for p in points]
    if len(pts) < 2:
        raise ValueError("至少需要两个路径点")
    rows: list[dict] = []
    price = pts[0]
    ts = ts_start
    targets = pts[1:] + ([pts[-1] + 0.4] if tail else [])
    for target in targets:
        rising = target >= price
        for i in range(bars_per_leg):
            open_ = price
            close = price + (target - price) / (bars_per_leg - i)
            last = i == bars_per_leg - 1
            if rising:
                high = close + (0.5 if last else 0.05)
                low = min(open_, close) - 0.05
            else:
                low = close - (0.5 if last else 0.05)
                high = max(open_, close) + 0.05
            rows.append(
                {
                    "ts_open": ts,
                    "open": round(open_, 6),
                    "high": round(high, 6),
                    "low": round(low, 6),
                    "close": round(close, 6),
                    "volume": volume,
                }
            )
            price = close
            ts += 60_000
    return rows


# §7.2：上升趋势线被收盘跌破，且最后一个高点低于前高（前极点测试失败）
REVERSAL_BULL = [100, 106, 103, 110, 106, 108.5, 101]

# §8.1：上升楔形（高点/低点均抬高，三推幅度 7→6→5，末段/首段≈0.71）
WEDGE_BULL = [100, 110, 103, 113, 107, 116, 111]

# §8.1：下降楔形（高点/低点均降低，三推幅度 10→8→5）
WEDGE_BEAR = [120, 110, 116, 108, 111, 106, 108]

# §8.3：抛物线楔形（三推幅度 6→8→11，斜率 1.2→1.6→2.2，比值 1.83）
PARABOLIC_BULL = [100, 108, 102, 116, 108, 131, 120]

# §6.6：对称三角形（高点降低、低点抬高、宽度收敛）
TRIANGLE = [100, 110, 102, 109, 104, 108, 105]

# §9.4/§9.6：两次回踩后再次上行（H2），用于计数与二次入场测试
SECOND_ENTRY = [100, 106, 102, 108, 103, 110, 105, 112, 108, 114]
