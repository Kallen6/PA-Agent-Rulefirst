"""导出单文件 HTML 决策报告（零第三方依赖）。

用法：
    python tools/export_html_report.py --regime broad_channel --setup
    python tools/export_html_report.py --regime tight_channel --llm mock
    python tools/export_html_report.py --live --timeframe 15m
    python tools/export_html_report.py --record records/2026-11-10/xx.json
    python tools/export_html_report.py --live --open

设计：
- 只用标准库：SVG 手绘 K 线，内联 CSS，无外链脚本/字体，可离线双击打开；
- `--regime` / `--live` 在内存里跑 `decide()`，因此报告含完整 K 线；
- `--record` 读取已保存记录，但记录不存 K 线，图表区降级为提示；
- 输出默认写入 `records/reports/`（已被 .gitignore 忽略）；
- 渲染层已抽取到 `pa_engine/report.py`，与 Web 决策台（`run.py --web`）共用。
"""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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


# ── 数据来源 ────────────────────────────────────────────────────────────────


def _make_llm(mode: str):
    if not mode:
        return None
    if mode == "mock":
        from pa_engine.ai.client import MockLLM

        return MockLLM()
    from pa_engine.ai.client import HttpGrayZoneClient

    return HttpGrayZoneClient.from_settings()


def _case_regime(kind: str, *, setup: bool, llm_mode: str, vote: int):
    from tests.fixtures.synthetic import make_regime_rows, make_setup_rows
    from tests.support import build_v2_frame

    rows = make_setup_rows(kind) if setup else make_regime_rows(kind)
    frame = build_v2_frame(rows)
    outcome = decide(frame, llm=_make_llm(llm_mode), vote=vote)
    label = f"synthetic:{kind}{'+setup' if setup else ''}"
    return frame, outcome, label


def _case_live(
    *,
    symbol: str | None,
    timeframe: str | None,
    bars: int,
    llm_mode: str,
    vote: int,
    offline: bool,
    offset: int = 0,
    base_url: str | None = None,
    proxy: str | None = None,
    timeout: int | None = None,
):
    import time
    from dataclasses import replace as dc_replace

    from pa_engine.config.settings import load_settings
    from pa_engine.data.binance import load_binance_frame
    from pa_engine.data.types import build_frame

    data = load_settings().data
    if symbol:
        data = dc_replace(data, symbol=symbol)
    if timeframe:
        data = dc_replace(data, timeframe=timeframe)
    if base_url:
        data = dc_replace(data, base_url=base_url)
    if timeout:
        data = dc_replace(data, request_timeout_seconds=max(3, int(timeout)))
    count = max(20, min(1000, bars or data.bar_count))

    if offline:
        from tools.baseline_measure import CACHE_DIR, _load_cache

        cached = _load_cache(data.symbol, data.timeframe)
        if not cached:
            available = (
                sorted(path.stem for path in CACHE_DIR.glob("*.json"))
                if CACHE_DIR.is_dir()
                else []
            )
            raise RuntimeError(
                f"离线缓存缺失 {data.symbol}_{data.timeframe}；可用："
                f"{', '.join(available) or '无（先跑 tools/baseline_measure.py）'}"
            )
        start = max(0, int(offset))
        if len(cached) < start + count:
            raise RuntimeError(
                f"缓存不足：需要 {start + count} 根，实际 {len(cached)} 根"
            )
        frame = build_frame(
            data.symbol,
            data.timeframe,
            cached[start : start + count],
            snapshot_ts_local_ms=int(time.time() * 1000),
        )
        source = f"cache:{frame.symbol}:{frame.timeframe}"
    else:
        if offset:
            raise RuntimeError("--offset 仅配合 --offline 使用（实时行情只能取最新窗口）")
        import urllib.request

        opener = None
        if proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            ).open
        frame = load_binance_frame(dc_replace(data, bar_count=count), opener=opener)
        source = f"binance:{frame.symbol}:{frame.timeframe}"

    outcome = decide(frame, llm=_make_llm(llm_mode), vote=vote)
    return frame, outcome, source


def _case_record(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
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

    return _Frame(), outcome, f"record:{path.name}"


# ── CLI ─────────────────────────────────────────────────────────────────────


def _default_out(symbol: str, timeframe: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in f"{symbol}_{timeframe}")
    return ROOT / "records" / "reports" / f"{safe}_{stamp}.html"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="导出单文件 HTML 决策报告（零依赖，可离线打开）"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--regime", choices=REGIMES, help="合成行情类型")
    group.add_argument("--live", action="store_true", help="抓取币安真实 K 线")
    group.add_argument("--record", help="已保存的 v2 记录 JSON（无 K 线，图表降级）")
    parser.add_argument("--setup", action="store_true", help="叠加合格信号棒")
    parser.add_argument("--symbol", default=None, help="--live 时覆盖 data.symbol")
    parser.add_argument("--timeframe", default=None, help="--live 时覆盖 data.timeframe")
    parser.add_argument("--bars", type=int, default=0, help="--live 时覆盖 data.bar_count")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="--live 时只读 records/baseline/klines 缓存，不联网",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="--live --offline 时从最新往前跳过 N 根，用于回看历史窗口",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="覆盖币安域名，如 https://data-api.binance.vision",
    )
    parser.add_argument(
        "--proxy",
        default=None,
        help="HTTP(S) 代理，如 http://127.0.0.1:7890",
    )
    parser.add_argument("--timeout", type=int, default=None, help="请求超时秒数（默认取配置）")
    parser.add_argument("--llm", nargs="?", const="mock", default="", help="mock / http，默认不调用")
    parser.add_argument("--vote", type=int, default=0, help="灰区自一致性投票次数")
    parser.add_argument("--out", default=None, help="输出 HTML 路径")
    parser.add_argument("--open", action="store_true", help="生成后用浏览器打开")
    args = parser.parse_args(argv)

    try:
        if args.regime:
            frame, outcome, source = _case_regime(
                args.regime, setup=args.setup, llm_mode=args.llm, vote=args.vote
            )
        elif args.live:
            frame, outcome, source = _case_live(
                symbol=args.symbol,
                timeframe=args.timeframe,
                bars=args.bars,
                llm_mode=args.llm,
                vote=args.vote,
                offline=args.offline,
                offset=args.offset,
                base_url=args.base_url,
                proxy=args.proxy,
                timeout=args.timeout,
            )
        else:
            path = Path(args.record)
            if not path.is_file():
                print(f"记录不存在：{path}")
                return 1
            frame, outcome, source = _case_record(path)
    except Exception as exc:  # 网络或数据异常，给出明确原因
        print(f"生成失败：{type(exc).__name__}: {exc}")
        return 2

    markup = render_html(frame, outcome, source=source)
    out = Path(args.out) if args.out else _default_out(
        str(getattr(frame, "symbol", "PA")), str(getattr(frame, "timeframe", ""))
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markup, encoding="utf-8")

    decision = outcome.decision
    print(
        f"HTML 报告：{out}（{out.stat().st_size / 1024:.1f} KB）\n"
        f"周期={decision.get('cycle_position')} 方向={decision.get('direction')} "
        f"终态={(decision.get('terminal') or {}).get('outcome')} "
        f"灰区调用={outcome.llm_called}"
    )
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
