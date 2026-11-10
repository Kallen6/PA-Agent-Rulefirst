"""币安适配器：URL 拼装、未收盘剔除、错误包装（全部离线，不发网络请求）。"""
from __future__ import annotations

import json
import urllib.error

from pa_engine.config.settings import DataSettings
from pa_engine.data.binance import (
    KlineFetchError,
    build_query,
    fetch_klines,
    klines_url,
    load_binance_frame,
    parse_klines,
)


def _row(ts_open: int, close_time: int, o: float, h: float, l: float, c: float, v: float):
    """币安 K 线 12 元素最小行。"""
    return [ts_open, str(o), str(h), str(l), str(c), str(v), close_time, "0", 0, "0", "0", "0"]


class _FakeResponse:
    def __init__(self, payload) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _opener(payload, captured: list):
    def _open(request, timeout=None):
        captured.append((request.full_url, timeout))
        return _FakeResponse(payload)

    return _open


def test_klines_url_uses_market_specific_path() -> None:
    assert klines_url(DataSettings(market="spot", base_url="")) == (
        "https://api.binance.com/api/v3/klines"
    )
    assert klines_url(DataSettings(market="futures", base_url="")) == (
        "https://fapi.binance.com/fapi/v1/klines"
    )
    assert klines_url(DataSettings(market="spot", base_url="https://proxy.local/")) == (
        "https://proxy.local/api/v3/klines"
    )


def test_build_query_encodes_params_and_clamps_limit() -> None:
    query = build_query(DataSettings(symbol="BTCUSDT", timeframe="15m", bar_count=5000))
    assert "symbol=BTCUSDT" in query
    assert "interval=15m" in query
    assert "limit=1000" in query
    assert "limit=1" in build_query(DataSettings(bar_count=0))
    # 多要一根抵消未收盘 K 线
    assert "limit=101" in build_query(DataSettings(bar_count=100))


def test_parse_klines_drops_unclosed_bar_and_orders_newest_first() -> None:
    rows = [
        _row(1_000, 1_999, 1, 2, 0.5, 1.5, 10),
        _row(2_000, 2_999, 2, 3, 1.5, 2.5, 20),
        _row(3_000, 3_999, 3, 4, 2.5, 3.5, 30),  # now=3500 < close_time，未收盘
    ]
    bars = parse_klines(rows, now_ms=3_500)
    assert [b.seq for b in bars] == [1, 2]
    assert [b.ts_open for b in bars] == [2_000, 1_000]
    assert bars[0].close == 2.5 and bars[0].volume == 20.0


def test_parse_klines_skips_malformed_rows() -> None:
    assert parse_klines([[1, "x", 2, 1, 1, 1, 2], "bad", None, []], now_ms=10) == []


def test_fetch_klines_passes_timeout_and_builds_frame() -> None:
    payload = [
        _row(1_000, 1_999, 1, 2, 0.5, 1.5, 10),
        _row(2_000, 2_999, 2, 3, 1.5, 2.5, 20),
    ]
    captured: list = []
    data = DataSettings(
        symbol="BTCUSDT", timeframe="15m", bar_count=100, request_timeout_seconds=7
    )

    bars = fetch_klines(data, now_ms=10_000, opener=_opener(payload, captured))
    assert len(bars) == 2
    url, timeout = captured[0]
    assert "symbol=BTCUSDT" in url and "interval=15m" in url and "limit=101" in url
    assert timeout == 7

    frame = load_binance_frame(data, now_ms=10_000, opener=_opener(payload, captured))
    assert frame.symbol == "BTCUSDT" and frame.timeframe == "15m"
    assert len(frame.bars) == 2
    assert len(frame.indicators.ema20) == len(frame.bars)


def test_fetch_klines_rejects_non_binance_source() -> None:
    try:
        fetch_klines(DataSettings(source="mt5"))
    except KlineFetchError as exc:
        assert "binance" in str(exc)
    else:
        raise AssertionError("非 binance 数据源应抛 KlineFetchError")


def test_fetch_klines_wraps_http_error() -> None:
    def _open(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 451, "Unavailable", None, None)

    try:
        fetch_klines(DataSettings(), opener=_open)
    except KlineFetchError as exc:
        assert "451" in str(exc)
    else:
        raise AssertionError("HTTP 错误应包装为 KlineFetchError")


def test_fetch_klines_rejects_empty_or_unclosed_payload() -> None:
    try:
        fetch_klines(DataSettings(), now_ms=10_000, opener=_opener([], []))
    except KlineFetchError as exc:
        assert "为空" in str(exc)
    else:
        raise AssertionError("空响应应抛 KlineFetchError")
