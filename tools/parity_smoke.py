"""v1/v2 特征 parity 冒烟：同一合成帧上逐字段比对。

用法：
    python tools/parity_smoke.py
"""
from __future__ import annotations

import math
import random
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "PA_Agent"))

# v1 的 pa_agent.util 会在 import 时拉起 PyQt6；parity 只用到纯计算，
# 这里放一个最小桩，避免安装 GUI 依赖。
if "PyQt6" not in sys.modules:
    pyqt6 = types.ModuleType("PyQt6")
    qtcore = types.ModuleType("PyQt6.QtCore")

    class _QObject:  # noqa: D401
        def __init__(self, *args, **kwargs) -> None:
            pass

    def _pyqtSignal(*args, **kwargs):
        return object()

    qtcore.QObject = _QObject
    qtcore.pyqtSignal = _pyqtSignal
    pyqt6.QtCore = qtcore
    sys.modules["PyQt6"] = pyqt6
    sys.modules["PyQt6.QtCore"] = qtcore

from pa_agent.ai.kline_features import compute_kline_geometry_features as v1_kline
from pa_agent.ai.market_features import compute_simple_market_features as v1_struct
from pa_agent.data.base import IndicatorBundle as V1Indicators
from pa_agent.data.base import KlineBar as V1Bar
from pa_agent.data.base import KlineFrame as V1Frame
from pa_agent.indicators.atr import atr_full as v1_atr
from pa_agent.indicators.ema import ema_full as v1_ema
from pa_engine.data.types import KlineBar, build_frame
from pa_engine.features.kline import compute_kline_geometry_features as v2_kline
from pa_engine.features.structure import compute_simple_market_features as v2_struct


def make_raw(n: int = 100, seed: int = 7) -> list[tuple]:
    rng = random.Random(seed)
    price = 100.0
    rows = []
    for i in range(n):
        o = price
        c = o + rng.uniform(-1, 1)
        h = max(o, c) + rng.uniform(0, 0.5)
        lo = min(o, c) - rng.uniform(0, 0.5)
        rows.append((i + 1, o, h, lo, c, 1000 + rng.random() * 500))
        price = c
    return rows


def build_pair(rows: list[tuple]):
    v1_bars, v2_bars = [], []
    for seq, (i, o, h, lo, c, v) in enumerate(reversed(rows), start=1):
        ts = 1_000_000 + i * 60_000
        v1_bars.append(V1Bar(seq=seq, ts_open=ts, open=o, high=h, low=lo, close=c, volume=v))
        v2_bars.append(KlineBar(seq=seq, ts_open=ts, open=o, high=h, low=lo, close=c, volume=v))

    closes = [b.close for b in reversed(v1_bars)]
    highs = [b.high for b in reversed(v1_bars)]
    lows = [b.low for b in reversed(v1_bars)]
    ema = v1_ema(closes, 20)
    atr = v1_atr(highs, lows, closes, 14)
    frame_v1 = V1Frame(
        symbol="TEST",
        timeframe="1h",
        bars=tuple(v1_bars),
        indicators=V1Indicators(ema20=tuple(reversed(ema)), atr14=tuple(reversed(atr))),
        snapshot_ts_local_ms=0,
    )
    frame_v2 = build_frame("TEST", "1h", v2_bars)
    return frame_v1, frame_v2


def main() -> int:
    frame_v1, frame_v2 = build_pair(make_raw())

    for name, a, b in (
        ("ema20", frame_v1.indicators.ema20, frame_v2.indicators.ema20),
        ("atr14", frame_v1.indicators.atr14, frame_v2.indicators.atr14),
    ):
        mismatches = sum(
            1 for x, y in zip(a, b) if not (math.isnan(x) and math.isnan(y)) and abs(x - y) > 1e-9
        )
        print(f"{name}: mismatches={mismatches}")

    f1 = v1_kline(frame_v1)
    f2 = v2_kline(frame_v2)
    diffs = [
        (x.seq, key)
        for x, y in zip(f1, f2)
        for key in x.__dict__
        if getattr(x, key) != getattr(y, key)
    ]
    print(f"kline features: field_diffs={len(diffs)} first={diffs[:5]}")

    s1 = v1_struct(frame_v1)
    s2 = v2_struct(frame_v2)
    skip = {"swings", "breakout_events", "measured_moves", "hl_count"}
    diffs = [key for key in s1.__dict__ if key not in skip and getattr(s1, key) != getattr(s2, key)]
    hl_diffs = [
        key
        for key in s1.hl_count.__dict__
        if getattr(s1.hl_count, key) != getattr(s2.hl_count, key)
    ]
    print(f"structure: field_diffs={diffs} hl_diffs={hl_diffs}")
    print(
        "collections:",
        f"swings {len(s1.swings)}/{len(s2.swings)}",
        f"events {len(s1.breakout_events)}/{len(s2.breakout_events)}",
        f"mm {len(s1.measured_moves)}/{len(s2.measured_moves)}",
    )
    return 0 if not diffs else 1


if __name__ == "__main__":
    raise SystemExit(main())
