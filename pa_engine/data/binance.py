"""币安 K 线抓取适配器（标准库 urllib，零第三方依赖）。

接口（现货，与用户提供的 curl 一致）：
    GET https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=15m&limit=100

- 现货：`{base}/api/v3/klines`；合约：`{base}/fapi/v1/klines`（返回结构一致）；
- `data.market` 决定域名：空 `data.base_url` 时 spot→`api.binance.com`、
  futures→`fapi.binance.com`（见 `DataSettings.resolved_base_url`）；
- 公共 K 线**免鉴权**，本模块不发 `X-MBX-APIKEY`；`data.api_key` 仅留给
  私有接口/代理，抓公共行情时填了也不会被读取；
- 响应每根 12 元素：`[openTime, open, high, low, close, volume, closeTime, ...]`。

约定（对齐 `pa_engine/data/types.py`）：
- 只保留**已收盘** K 线（`now_ms >= closeTime`），正在形成的那根丢弃；
- 输出「最新在前」、`seq` 从 1 递增，可直接交给 `build_frame`。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Iterable, Sequence

from pa_engine.config.settings import DataSettings
from pa_engine.data.types import KlineBar, KlineFrame, build_frame

KLINE_PATHS: dict[str, str] = {
    "spot": "/api/v3/klines",
    "futures": "/fapi/v1/klines",
}
MAX_LIMIT = 1000  # 币安单次上限

# urlopen 的替身签名：open(request, timeout=...) -> 支持 with 的响应对象
Opener = Callable[..., Any]


class KlineFetchError(RuntimeError):
    """抓取失败（网络 / HTTP / 响应格式）。消息可直接展示给用户，不含密钥。"""


def klines_url(data: DataSettings) -> str:
    """K 线 REST 地址（不含查询串）；未知 market 按现货路径处理。"""
    path = KLINE_PATHS.get(data.market, KLINE_PATHS["spot"])
    return f"{data.resolved_base_url}{path}"


def build_query(data: DataSettings) -> str:
    """查询串：symbol / interval / limit。

    多要一根（`bar_count + 1`）用来抵消「正在形成、会被丢弃」的那根，
    保证分析帧稳定拿到配置根数；limit 夹在 1–1000。
    """
    limit = max(1, min(int(data.bar_count) + 1, MAX_LIMIT))
    return urllib.parse.urlencode(
        {"symbol": data.symbol, "interval": data.timeframe, "limit": str(limit)}
    )


def parse_klines(
    raw_rows: Iterable[Sequence[Any]],
    *,
    now_ms: int | None = None,
) -> list[KlineBar]:
    """币安原始数组 → 「最新在前」的已收盘 `KlineBar` 列表（seq 从 1 开始）。

    未收盘（`now < closeTime`）、字段不足 6 个、非数值的行会被跳过。
    """
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    parsed: list[tuple[int, tuple[float, ...]]] = []
    for row in raw_rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            ts_open = int(row[0])
            values = tuple(float(row[i]) for i in (1, 2, 3, 4, 5))
        except (TypeError, ValueError):
            continue
        close_time = int(row[6]) if len(row) > 6 else ts_open
        if now < close_time:  # 仍在形成，丢弃
            continue
        parsed.append((ts_open, values))

    parsed.sort(key=lambda item: item[0], reverse=True)
    return [
        KlineBar(
            seq=seq,
            ts_open=ts_open,
            open=values[0],
            high=values[1],
            low=values[2],
            close=values[3],
            volume=values[4],
        )
        for seq, (ts_open, values) in enumerate(parsed, start=1)
    ]


def _http_detail(exc: urllib.error.HTTPError) -> str:
    """尽量取出币安返回的 `{"code":..,"msg":..}`，取不到就退回原因短语。"""
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - 诊断信息失败不影响主流程
        return str(exc.reason)
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return body[:200] or str(exc.reason)
    if isinstance(payload, dict) and payload.get("msg"):
        return f"{payload.get('code', '')} {payload['msg']}".strip()
    return body[:200]


def fetch_klines(
    data: DataSettings | None = None,
    *,
    now_ms: int | None = None,
    opener: Opener | None = None,
) -> list[KlineBar]:
    """按配置抓取一轮 K 线；失败统一抛 `KlineFetchError`。"""
    config = data or DataSettings()
    if config.source != "binance":
        raise KlineFetchError(
            f"data.source={config.source!r} 不支持：当前只实现了 binance 适配器"
        )
    if not config.symbol:
        raise KlineFetchError("data.symbol 未配置（如 BTCUSDT）")

    url = f"{klines_url(config)}?{build_query(config)}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "re_pa_agent/2"}, method="GET"
    )
    open_fn: Opener = opener or urllib.request.urlopen
    try:
        with open_fn(request, timeout=config.request_timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise KlineFetchError(f"HTTP {exc.code}：{_http_detail(exc)}（{url}）") from exc
    except urllib.error.URLError as exc:
        raise KlineFetchError(f"网络不可达：{exc.reason}（{url}）") from exc
    except (TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise KlineFetchError(f"响应异常：{exc}（{url}）") from exc

    if not isinstance(payload, list):
        raise KlineFetchError(f"响应不是 K 线数组：{str(payload)[:200]}")

    bars = parse_klines(payload, now_ms=now_ms)
    if not bars:
        raise KlineFetchError("返回的 K 线为空或全部未收盘")
    return bars


def load_binance_frame(
    data: DataSettings | None = None,
    *,
    now_ms: int | None = None,
    opener: Opener | None = None,
) -> KlineFrame:
    """抓取并构造可直接送入 `decide()` 的 `KlineFrame`。"""
    config = data or DataSettings()
    bars = fetch_klines(config, now_ms=now_ms, opener=opener)
    return build_frame(
        config.symbol,
        config.timeframe,
        bars,
        snapshot_ts_local_ms=int(time.time() * 1000) if now_ms is None else int(now_ms),
    )


def build_history_query(
    *,
    symbol: str,
    timeframe: str,
    limit: int,
    end_time_ms: int,
) -> str:
    """历史分页查询串：按 endTime 向过去翻页，单次不超过 1000 根。"""
    return urllib.parse.urlencode(
        {
            "symbol": symbol,
            "interval": timeframe,
            "limit": str(max(1, min(int(limit), MAX_LIMIT))),
            "endTime": str(int(end_time_ms)),
        }
    )


def _request_klines(
    url: str,
    *,
    timeout: float,
    opener: Opener,
) -> list[Sequence[Any]]:
    request = urllib.request.Request(
        url, headers={"User-Agent": "re_pa_agent/2"}, method="GET"
    )
    payload = json.loads(opener(request, timeout=timeout).read().decode("utf-8"))
    if not isinstance(payload, list):
        raise KlineFetchError(f"响应不是 K 线数组：{str(payload)[:200]}")
    return payload


def fetch_klines_history(
    *,
    symbol: str,
    timeframe: str,
    count: int,
    data: DataSettings | None = None,
    now_ms: int | None = None,
    opener: Opener | None = None,
    request_interval_seconds: float = 0.15,
    max_retries: int = 3,
    sleep: Callable[[float], None] | None = None,
) -> list[KlineBar]:
    """分页抓取历史已收盘 K 线（最新在前，seq 从 1 开始）。

    - 从 `now_ms` 向过去翻页，使用 `endTime`；
    - 单次最多 `MAX_LIMIT` 根，去重后按时间倒序；
    - 429/418/5xx 指数退避重试（0.25s、0.5s、1s），其它 HTTP 错误直接失败；
    - 不发送任何鉴权头。
    """
    config = data or DataSettings(symbol=symbol, timeframe=timeframe)
    base_url = config.resolved_base_url
    path = KLINE_PATHS.get(config.market, KLINE_PATHS["spot"])
    open_fn: Opener = opener or urllib.request.urlopen
    wait = sleep or time.sleep
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    target = max(1, int(count))
    collected: dict[int, KlineBar] = {}
    end_time = now
    attempts = 0

    while len(collected) < target:
        remaining = min(target - len(collected) + 1, MAX_LIMIT)
        query = build_history_query(
            symbol=symbol, timeframe=timeframe, limit=remaining, end_time_ms=end_time
        )
        url = f"{base_url}{path}?{query}"
        rows: list[Sequence[Any]] | None = None
        while attempts < max_retries:
            attempts += 1
            try:
                rows = _request_klines(url, timeout=config.request_timeout_seconds, opener=open_fn)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (418, 429) or 500 <= int(exc.code) < 600:
                    if attempts >= max_retries:
                        raise KlineFetchError(
                            f"HTTP {exc.code}：重试 {max_retries} 次仍失败（{url}）"
                        ) from exc
                    wait(0.25 * (2 ** (attempts - 1)))
                    continue
                raise KlineFetchError(f"HTTP {exc.code}：{_http_detail(exc)}（{url}）") from exc
            except urllib.error.URLError as exc:
                raise KlineFetchError(f"网络不可达：{exc.reason}（{url}）") from exc
            except (TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise KlineFetchError(f"响应异常：{exc}（{url}）") from exc
        attempts = 0
        if not rows:
            break
        parsed = parse_klines(rows, now_ms=now)
        if not parsed:
            break
        for bar in parsed:
            collected.setdefault(int(bar.ts_open), bar)
        earliest = min(int(bar.ts_open) for bar in parsed)
        if earliest >= end_time:
            break
        end_time = earliest - 1
        if request_interval_seconds > 0:
            wait(request_interval_seconds)

    ordered = [collected[key] for key in sorted(collected, reverse=True)][:target]
    return [
        KlineBar(
            seq=seq,
            ts_open=bar.ts_open,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
        )
        for seq, bar in enumerate(ordered, start=1)
    ]


def load_history_frame(
    *,
    symbol: str,
    timeframe: str,
    count: int,
    data: DataSettings | None = None,
    now_ms: int | None = None,
    opener: Opener | None = None,
) -> KlineFrame:
    bars = fetch_klines_history(
        symbol=symbol,
        timeframe=timeframe,
        count=count,
        data=data,
        now_ms=now_ms,
        opener=opener,
    )
    return build_frame(symbol, timeframe, bars)


__all__ = [
    "KLINE_PATHS",
    "MAX_LIMIT",
    "KlineFetchError",
    "build_query",
    "fetch_klines",
    "klines_url",
    "load_binance_frame",
    "parse_klines",
]
