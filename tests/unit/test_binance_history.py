"""币安历史分页抓取单测（假 opener，不联网）。"""
from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse

from pa_engine.data.binance import (
    KlineFetchError,
    build_history_query,
    fetch_klines_history,
)

NOW = 2_000_000_000_000


def _row(open_time: int, close: float = 100.0) -> list:
    return [
        open_time,
        str(close),
        str(close + 1),
        str(close - 1),
        str(close + 0.5),
        "10",
        open_time + 59_999,
        "0",
        0,
        "0",
        "0",
        "0",
    ]


class _Resp:
    def __init__(self, rows):
        self._payload = json.dumps(rows).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_opener(calls: list[dict], *, limit_bars: int = 1000):
    def opener(request, timeout=10):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        end = int(query["endTime"][0])
        limit = int(query["limit"][0])
        calls.append({"end": end, "limit": limit, "timeout": timeout})
        count = min(limit, limit_bars)
        rows = [_row(end - i * 60_000, 100 + i) for i in range(count)]
        return _Resp(rows)

    return opener


def test_history_query_contains_symbol_interval_endtime() -> None:
    query = build_history_query(symbol="BTCUSDT", timeframe="1h", limit=1200, end_time_ms=NOW)
    parsed = urllib.parse.parse_qs(query)
    assert parsed["symbol"] == ["BTCUSDT"]
    assert parsed["interval"] == ["1h"]
    assert parsed["limit"] == ["1000"]
    assert parsed["endTime"] == [str(NOW)]


def test_pagination_returns_requested_count_newest_first() -> None:
    calls: list[dict] = []
    bars = fetch_klines_history(
        symbol="BTCUSDT",
        timeframe="1m",
        count=2500,
        now_ms=NOW,
        opener=_fake_opener(calls),
        request_interval_seconds=0,
    )
    assert len(bars) == 2500
    assert bars[0].seq == 1 and bars[-1].seq == 2500
    assert bars[0].ts_open > bars[-1].ts_open
    assert len(calls) == 3
    # 第二页的 endTime 必须早于第一页最早一根
    assert calls[1]["end"] < calls[0]["end"]


def test_unclosed_bar_is_dropped() -> None:
    closed_ts = NOW - 120_000

    def opener(request, timeout=10):
        rows = [
            _row(closed_ts, 100),
            [NOW - 1_000, "1", "2", "0.5", "1.5", "1", NOW + 59_999, "0", 0, "0", "0", "0"],
        ]
        return _Resp(rows)

    bars = fetch_klines_history(
        symbol="BTCUSDT",
        timeframe="1m",
        count=2,
        now_ms=NOW,
        opener=opener,
        request_interval_seconds=0,
    )
    assert len(bars) == 1
    assert bars[0].ts_open == closed_ts


def test_429_is_retried_then_succeeds() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def opener(request, timeout=10):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "rate limit", {}, io.BytesIO(b"{}"))
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        end = int(query["endTime"][0])
        return _Resp([_row(end - (i + 1) * 60_000) for i in range(5)])

    bars = fetch_klines_history(
        symbol="BTCUSDT",
        timeframe="1m",
        count=5,
        now_ms=NOW,
        opener=opener,
        request_interval_seconds=0,
        sleep=sleeps.append,
    )
    assert len(bars) == 5
    assert calls["n"] == 2
    assert sleeps and sleeps[0] > 0


def test_non_retryable_http_error_raises_clear_error() -> None:
    def opener(request, timeout=10):
        raise urllib.error.HTTPError(request.full_url, 400, "bad request", {}, io.BytesIO(b'{"msg":"x"}'))

    try:
        fetch_klines_history(
            symbol="BTCUSDT",
            timeframe="1m",
            count=5,
            now_ms=NOW,
            opener=opener,
            request_interval_seconds=0,
        )
    except KlineFetchError as exc:
        assert "HTTP 400" in str(exc)
    else:
        raise AssertionError("应抛出 KlineFetchError")
