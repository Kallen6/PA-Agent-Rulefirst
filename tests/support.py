"""测试支撑：路径注入、PyQt6 桩、v1 双帧构造。

设计目的：
- v1 项目只作只读 parity 来源，测试环境可能没有 PyQt6；
- 同一份 bars 同时构造 v1/v2 帧，指标用 v1 计算后共享，隔离“指标差异”；
- v1 不可用时测试显式跳过（`skip`），不静默通过。
"""
from __future__ import annotations

import math
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(WORKSPACE / "PA_Agent"))


def install_pyqt_stub() -> None:
    if "PyQt6" in sys.modules:
        return
    pyqt6 = types.ModuleType("PyQt6")
    qtcore = types.ModuleType("PyQt6.QtCore")

    class _QObject:
        def __init__(self, *args, **kwargs) -> None:
            pass

    def _pyqtSignal(*args, **kwargs):
        return object()

    qtcore.QObject = _QObject
    qtcore.pyqtSignal = _pyqtSignal
    pyqt6.QtCore = qtcore
    sys.modules["PyQt6"] = pyqt6
    sys.modules["PyQt6.QtCore"] = qtcore


def v1_available() -> bool:
    install_pyqt_stub()
    try:
        import pa_agent.ai.market_features  # noqa: F401

        return True
    except Exception:
        return False


V1_OK = v1_available()


def make_raw_rows(n: int = 100, seed: int = 7, start: float = 100.0) -> list[dict]:
    """构造确定性随机游走（oldest-first）。"""
    import random

    rng = random.Random(seed)
    price = start
    rows: list[dict] = []
    for i in range(n):
        open_ = price
        close = open_ + rng.uniform(-1, 1)
        high = max(open_, close) + rng.uniform(0, 0.5)
        low = min(open_, close) - rng.uniform(0, 0.5)
        rows.append(
            {
                "ts_open": 1_000_000 + i * 60_000,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000 + rng.random() * 500,
            }
        )
        price = close
    return rows


def to_newest_first(rows: list[dict]) -> list[dict]:
    out = []
    for seq, row in enumerate(reversed(rows), start=1):
        item = dict(row)
        item["seq"] = seq
        out.append(item)
    return out


def build_dual_frames(rows: list[dict]):
    """返回 (frame_v1, frame_v2)，指标由 v1 计算后共享。"""
    install_pyqt_stub()
    from pa_agent.data.base import IndicatorBundle as V1Indicators
    from pa_agent.data.base import KlineBar as V1Bar
    from pa_agent.data.base import KlineFrame as V1Frame
    from pa_agent.indicators.atr import atr_full as v1_atr
    from pa_agent.indicators.ema import ema_full as v1_ema

    from pa_engine.data.types import KlineBar, KlineFrame, IndicatorBundle

    bars = to_newest_first(rows)
    v1_bars = [
        V1Bar(
            seq=b["seq"],
            ts_open=b["ts_open"],
            open=b["open"],
            high=b["high"],
            low=b["low"],
            close=b["close"],
            volume=b["volume"],
        )
        for b in bars
    ]
    v2_bars = [
        KlineBar(
            seq=b["seq"],
            ts_open=b["ts_open"],
            open=b["open"],
            high=b["high"],
            low=b["low"],
            close=b["close"],
            volume=b["volume"],
        )
        for b in bars
    ]

    chrono = list(reversed(v1_bars))
    closes = [b.close for b in chrono]
    highs = [b.high for b in chrono]
    lows = [b.low for b in chrono]
    ema = tuple(reversed(v1_ema(closes, 20)))
    atr = tuple(reversed(v1_atr(highs, lows, closes, 14)))

    frame_v1 = V1Frame(
        symbol="TEST",
        timeframe="1h",
        bars=tuple(v1_bars),
        indicators=V1Indicators(ema20=ema, atr14=atr),
        snapshot_ts_local_ms=0,
    )
    frame_v2 = KlineFrame(
        symbol="TEST",
        timeframe="1h",
        bars=tuple(v2_bars),
        indicators=IndicatorBundle(ema20=ema, atr14=atr),
        snapshot_ts_local_ms=0,
    )
    return frame_v1, frame_v2


def build_v2_frame(rows: list[dict], symbol: str = "TEST", timeframe: str = "1h"):
    """由 oldest-first 行构造 v2 帧（指标按 v2 实现计算）。"""
    from pa_engine.data.types import KlineBar, build_frame

    bars = [
        KlineBar(
            seq=seq,
            ts_open=row["ts_open"],
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row.get("volume", 0.0),
        )
        for seq, row in enumerate(reversed(rows), start=1)
    ]
    return build_frame(symbol, timeframe, bars)


def nan_equal(a, b, tol: float = 1e-9) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True
        return abs(a - b) <= tol
    return a == b
