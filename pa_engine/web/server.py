"""Web 决策台：本地单页控制台的后端（仅标准库，零第三方依赖）。

设计：
- 数据拉取/决策全部在服务端执行（浏览器直连币安受 CORS 限制且网络被阻断）；
- 复用 `fetch_klines`（open_fn 注入代理）与 `decide()`（纯内存、无落盘）；
- 报告复用 `pa_engine.report.render_html`（与 CLI 同一渲染路径）；
- 默认零文件：仅前端显式勾选"保存记录"才调用 `save_decision`；
- 安全边界：只绑 127.0.0.1；api_key 等敏感配置不进任何响应；
  读取历史记录时校验路径必须位于 records 根目录内。
"""
from __future__ import annotations

import json
import sys
import urllib.request
from dataclasses import replace as dc_replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pa_engine.engine.pipeline import DecisionOutcome, decide  # noqa: E402
from pa_engine.report import render_html  # noqa: E402

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
TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d")
STATIC_DIR = ROOT / "web_static"
DEFAULT_RECORDS_DIR = ROOT / "records"

MAX_BODY_BYTES = 64 * 1024


# ── 数据编排（无渲染副作用） ────────────────────────────────────────────────


def _make_llm(mode: str):
    if not mode:
        return None
    if mode == "mock":
        from pa_engine.ai.client import MockLLM

        return MockLLM()
    from pa_engine.ai.client import HttpGrayZoneClient

    return HttpGrayZoneClient.from_settings()


def _build_opener(proxy: str | None):
    if not proxy:
        return None
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    ).open


def _decide_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    """payload → decide() 关键字参数；band 未提供时不覆盖默认灰区带宽。"""
    kwargs: dict[str, Any] = {
        "llm": _make_llm(str(payload.get("llm") or "")),
        "vote": int(payload.get("vote") or 0),
    }
    band = payload.get("band")
    if band is not None and str(band).strip() != "":
        value = float(band)
        if not 0 < value <= 0.5:
            raise ValueError(f"灰区带宽超出合理范围（0,0.5]：{value}")
        kwargs["band"] = value
    return kwargs


def _case_regime(payload: dict[str, Any]) -> tuple[Any, DecisionOutcome, str]:
    from tests.fixtures.synthetic import make_regime_rows, make_setup_rows
    from tests.support import build_v2_frame

    kind = str(payload.get("regime") or "broad_channel")
    if kind not in REGIMES:
        raise ValueError(f"未知合成行情类型：{kind}")
    rows = make_setup_rows(kind) if payload.get("setup") else make_regime_rows(kind)
    frame = build_v2_frame(rows)
    outcome = decide(frame, **_decide_kwargs(payload))
    label = f"synthetic:{kind}{'+setup' if payload.get('setup') else ''}"
    return frame, outcome, label


def _case_live(payload: dict[str, Any]) -> tuple[Any, DecisionOutcome, str]:
    from pa_engine.config.settings import load_settings
    from pa_engine.data.binance import load_binance_frame

    data = load_settings().data
    symbol = str(payload.get("symbol") or "").strip().upper()
    timeframe = str(payload.get("timeframe") or "").strip()
    market = str(payload.get("market") or "").strip().lower()
    base_url = str(payload.get("base_url") or "").strip()
    proxy = str(payload.get("proxy") or "").strip()
    if symbol:
        data = dc_replace(data, symbol=symbol)
    if timeframe:
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"不支持的周期：{timeframe}")
        data = dc_replace(data, timeframe=timeframe)
    if market in ("spot", "futures"):
        data = dc_replace(data, market=market)
    if base_url:
        data = dc_replace(data, base_url=base_url)
    count = max(20, min(1000, int(payload.get("bar_count") or data.bar_count)))
    data = dc_replace(data, bar_count=count)

    opener = _build_opener(proxy or None)
    frame = load_binance_frame(data, opener=opener)
    outcome = decide(frame, **_decide_kwargs(payload))
    return frame, outcome, f"binance:{frame.symbol}:{frame.timeframe}"


def _case_record(payload_path: Path) -> tuple[Any, DecisionOutcome, str]:
    """已保存记录 → 无 K 线降级报告（复用 CLI _case_record 的 stub 形状）。"""
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    decision = payload.get("decision") or {}
    stage1 = payload.get("stage1") or {}
    gray_raw = payload.get("gray_zone") or {}

    class _Gray:
        need_llm = bool(gray_raw.get("need_llm"))
        contested_nodes = tuple(gray_raw.get("contested") or ())
        reasons = tuple(gray_raw.get("reasons") or ())

    outcome = DecisionOutcome(
        decision=decision,
        stage1=stage1,
        gate_results=[],
        gray_zone=_Gray(),  # type: ignore[arg-type]
        llm_called=bool(payload.get("llm_called")),
        llm_answer=payload.get("llm_answer"),
        prompt_stats=payload.get("prompt_stats") or {},
    )

    class _Frame:
        symbol = payload.get("symbol") or "—"
        timeframe = payload.get("timeframe") or "—"
        bars: tuple = ()
        indicators = None

    return _Frame(), outcome, f"record:{payload_path.name}"


def _summary(frame: Any, outcome: DecisionOutcome, source: str) -> dict[str, Any]:
    decision = outcome.decision
    return {
        "source": source,
        "symbol": getattr(frame, "symbol", None),
        "timeframe": getattr(frame, "timeframe", None),
        "cycle_position": decision.get("cycle_position"),
        "direction": decision.get("direction"),
        "terminal": decision.get("terminal") or {},
        "order_type": decision.get("order_type"),
        "llm_called": outcome.llm_called,
    }


def _records_root(records_dir: str | None) -> Path:
    return Path(records_dir).resolve() if records_dir else DEFAULT_RECORDS_DIR.resolve()


def _run_decide(payload: dict[str, Any], records_dir: str | None) -> dict[str, Any]:
    source_mode = str(payload.get("source") or "regime")
    if source_mode == "live":
        frame, outcome, source = _case_live(payload)
    else:
        frame, outcome, source = _case_regime(payload)

    saved_path: str | None = None
    if payload.get("save"):
        from pa_engine.records.writer import save_decision

        bars = getattr(frame, "bars", ()) or ()
        anchor_ts = float(bars[0].ts_open) if bars else None
        path = save_decision(
            outcome,
            symbol=str(getattr(frame, "symbol", "PA")),
            timeframe=str(getattr(frame, "timeframe", "")),
            records_dir=records_dir,
            anchor_ts=anchor_ts,
        )
        saved_path = str(path)

    report = render_html(frame, outcome, source=source, title="网页决策台 · 实时决策")
    return {
        "ok": True,
        "summary": _summary(frame, outcome, source),
        "report_html": report,
        "saved_path": saved_path,
    }


def _list_records(records_dir: str | None, limit: int = 30) -> dict[str, Any]:
    from pa_engine.records.history import recent_records

    items = []
    for payload in recent_records(records_dir=records_dir, limit=limit):
        decision = payload.get("decision") or {}
        terminal = decision.get("terminal") or {}
        items.append(
            {
                "path": payload.get("record_path") or "",
                "generated_at": payload.get("generated_at") or "",
                "symbol": payload.get("symbol"),
                "timeframe": payload.get("timeframe"),
                "cycle_position": decision.get("cycle_position"),
                "direction": decision.get("direction"),
                "outcome": terminal.get("outcome"),
                "node_id": terminal.get("node_id"),
            }
        )
    return {"ok": True, "records": items}


def _load_record(query: dict[str, list[str]], records_dir: str | None) -> dict[str, Any]:
    raw = (query.get("path") or [""])[0]
    if not raw:
        raise ValueError("缺少 path 参数")
    root = _records_root(records_dir)
    target = Path(raw)
    if not target.is_absolute():
        target = root / target
    target = target.resolve()
    if root not in target.parents and target != root:
        raise PermissionError("记录路径越界")
    if not target.is_file():
        raise FileNotFoundError(f"记录不存在：{target.name}")
    frame, outcome, source = _case_record(target)
    report = render_html(frame, outcome, source=source, title=f"历史决策 · {target.name}")
    return {"ok": True, "summary": _summary(frame, outcome, source), "report_html": report}


# ── HTTP 服务 ───────────────────────────────────────────────────────────────


class DecisionConsoleHandler(BaseHTTPRequestHandler):
    server_version = "PANetConsole/1.0"
    records_dir: str | None = None

    # ── 响应工具 ──

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, name: str, content_type: str) -> None:
        path = STATIC_DIR / name
        if not path.is_file():
            self._send_json({"ok": False, "error": f"静态资源缺失：{name}"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    # ── 路由 ──

    def do_GET(self) -> None:  # noqa: N802（BaseHTTPRequestHandler 契约）
        parsed = urlparse(self.path)
        route = parsed.path
        try:
            if route in ("/", "/index.html"):
                self._send_static("index.html", "text/html; charset=utf-8")
            elif route == "/app.js":
                self._send_static("app.js", "text/javascript; charset=utf-8")
            elif route == "/style.css":
                self._send_static("style.css", "text/css; charset=utf-8")
            elif route == "/api/records":
                self._send_json(_list_records(self.records_dir))
            elif route == "/api/record":
                self._send_json(_load_record(parse_qs(parsed.query), self.records_dir))
            else:
                self._send_json({"ok": False, "error": "未知路径"}, 404)
        except (ValueError, FileNotFoundError, PermissionError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:  # 兜底：单请求异常不拖垮服务
            self._send_json(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500
            )

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/decide":
            self._send_json({"ok": False, "error": "未知路径"}, 404)
            return
        try:
            payload = self._read_body()
            self._send_json(_run_decide(payload, self.records_dir))
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            # 网络不可达等：给出镜像/代理建议（与 CLI 报告工具口径一致）
            hint = ""
            text = str(exc)
            if "10054" in text or "timed out" in text.lower() or "URLError" in type(exc).__name__:
                hint = "；可尝试镜像域名 https://data-api.binance.vision 或配置本地代理"
            self._send_json(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}{hint}"}, 502
            )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        sys.stderr.write(
            f"[web] {self.address_string()} - {format % args}\n"
        )


def create_server(
    host: str = "127.0.0.1",
    port: int = 8788,
    records_dir: str | None = None,
) -> ThreadingHTTPServer:
    handler = type(
        "BoundHandler",
        (DecisionConsoleHandler,),
        {"records_dir": records_dir},
    )
    return ThreadingHTTPServer((host, port), handler)


def serve(host: str = "127.0.0.1", port: int = 8788, records_dir: str | None = None) -> None:
    httpd = create_server(host, port, records_dir)
    print(f"决策台已启动：http://{host}:{port}  （Ctrl+C 停止）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
