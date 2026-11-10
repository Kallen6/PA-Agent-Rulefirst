"""re_pa_agent 入口。

    python run.py --check                 列出已注册闸门
    python run.py --check-config          检查 config/settings.json 必填项
    python run.py --demo                  在空帧上运行已注册闸门
    python run.py --regime tight_channel  在合成行情上跑完整决策
    python run.py --live                  抓取真实币安 K 线后跑决策（默认保存记录）
    python run.py --live --incremental    读取上次记录作为灰区锚点
    python run.py --live --vote 3         灰区自一致性投票（3 次取多数）
    python run.py --regime spike --llm    灰区使用离线 Mock（不联网）
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

from pa_engine.engine.pipeline import decide
from pa_engine.gates.base import GATES, GateContext, run_all

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


@dataclass
class _DummyFrame:
    bars: tuple = ()
    indicators: object | None = None


def _cmd_check() -> int:
    if not GATES:
        print("尚未注册任何闸门")
        return 0
    for node_id in sorted(GATES):
        fn = GATES[node_id]
        print(f"{node_id:<6} {fn.__module__}.{fn.__name__}")
    return 0


def _cmd_check_config() -> int:
    from pa_engine.config.paths import SETTINGS_JSON_PATH
    from pa_engine.config.settings import load_settings, validate_settings

    settings = load_settings()
    data = settings.data
    print(f"配置文件: {SETTINGS_JSON_PATH}")
    print(f"数据源: {data.source} / {data.symbol} / {data.timeframe} ×{data.bar_count}")
    print(f"市场: {data.market}  base_url: {data.resolved_base_url}")
    print(
        f"LLM: enabled={settings.engine.llm_enabled}  "
        f"key={'已配置' if settings.provider.api_key else '未配置'}  "
        f"model={settings.provider.model}"
    )
    issues = validate_settings(settings)
    if issues:
        print("配置检查未通过：")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    print("配置检查通过")
    return 0


def _cmd_demo() -> int:
    results = run_all(_DummyFrame(), GateContext())
    for r in results:
        print(
            f"[{r.node_id}] answer={r.answer!r} branch={r.branch!r} "
            f"confidence={r.confidence:.2f} by={r.filled_by} reason={r.reason}"
        )
    return 0


def _make_llm(llm_mode: str):
    """mock=离线替身；http=按 config/settings.json 调真实 API；空=不启用。"""
    if llm_mode == "mock":
        from pa_engine.ai.client import MockLLM

        return MockLLM()
    if llm_mode == "http":
        from pa_engine.ai.client import HttpGrayZoneClient

        client = HttpGrayZoneClient.from_settings()
        if not client.enabled:
            print(
                "[警告] config/settings.json 未启用感知层"
                "（engine.llm_enabled / provider.base_url / provider.api_key），"
                "调用失败会记录 llm_error 并保留代码决策"
            )
        return client
    return None


def _print_outcome(outcome, as_json: bool) -> int:
    decision = outcome.decision

    if as_json:
        print(json.dumps(decision, ensure_ascii=False, indent=2))
        return 0

    print(f"周期: {decision['cycle_position']}  方向: {decision['direction']}  "
          f"AlwaysIn: {decision['always_in']}  诊断置信: {decision['diagnosis_confidence']}")
    print(f"闸门结论: {decision['gate_result']}  下单方式: {decision['order_type']}  "
          f"终态: {decision['terminal']['outcome']}/{decision['terminal']['node_id']}")
    for key in ("entry_price", "stop_loss_price", "take_profit_price", "take_profit_price_2"):
        if decision.get(key) is not None:
            print(f"  {key}: {decision[key]}")
    if decision.get("estimated_win_rate"):
        print(f"  估算胜率: {decision['estimated_win_rate']:.0f}%")
    print(f"原因: {decision['reason']}")
    print(f"灰区: need_llm={outcome.gray_zone.need_llm} 调用={outcome.llm_called} "
          f"prompt={outcome.prompt_stats.get('bytes', 0)}B")
    ballot = decision.get("vote")
    if ballot:
        print(
            f"自一致性投票: 有效={ballot['valid']}/{ballot['samples']} "
            f"一致率={ballot['agree_ratio']:.0%} 分歧={ballot['disagreements'] or '无'}"
        )
    incremental = decision.get("incremental")
    if incremental:
        print(
            f"增量锚定: 上次 {incremental['generated_at']} "
            f"新增 {incremental['new_bars']} 根（仅作灰区参考）"
        )
    if decision.get("llm_error"):
        print(f"感知层错误: {decision['llm_error']}")
    for reason in outcome.gray_zone.reasons:
        print(f"  - {reason}")
    return 0


def _save_outcome(outcome, *, symbol: str, timeframe: str, records_dir, anchor_ts) -> None:
    from pa_engine.records.writer import save_decision

    try:
        path = save_decision(
            outcome,
            symbol=symbol,
            timeframe=timeframe,
            records_dir=records_dir,
            anchor_ts=anchor_ts,
        )
    except OSError as exc:  # 落盘失败不影响决策输出
        print(f"[记录写入失败] {exc}")
        return
    print(f"记录: {path}")


def _cmd_regime(
    kind: str,
    *,
    llm_mode: str,
    as_json: bool,
    setup: bool,
    vote: int = 0,
    save: bool = False,
    records_dir=None,
) -> int:
    from tests.fixtures.synthetic import make_regime_rows, make_setup_rows
    from tests.support import build_v2_frame

    rows = make_setup_rows(kind) if setup else make_regime_rows(kind)
    frame = build_v2_frame(rows)
    outcome = decide(frame, llm=_make_llm(llm_mode), vote=vote)
    code = _print_outcome(outcome, as_json)
    if save:
        newest = frame.bars[0] if frame.bars else None
        _save_outcome(
            outcome,
            symbol=frame.symbol,
            timeframe=frame.timeframe,
            records_dir=records_dir,
            anchor_ts=float(newest.ts_open) if newest else None,
        )
    return code


def _fmt_utc(ts_ms: float) -> str:
    import datetime as _dt

    moment = _dt.datetime.fromtimestamp(ts_ms / 1000, _dt.timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def _cmd_live(
    *,
    llm_mode: str,
    as_json: bool,
    vote: int = 0,
    save: bool = True,
    records_dir=None,
    incremental: bool = False,
) -> int:
    """真实行情：按 config/settings.json 的 data.* 抓币安 K 线后跑决策。"""
    from pa_engine.config.settings import load_settings
    from pa_engine.data.binance import KlineFetchError, load_binance_frame

    data = load_settings().data
    try:
        frame = load_binance_frame(data)
    except KlineFetchError as exc:
        print(f"[数据源错误] {exc}")
        return 2
    newest = frame.bars[0] if frame.bars else None
    print(
        f"数据源: {data.source} {frame.symbol} {frame.timeframe} bars={len(frame.bars)} "
        f"最新收盘: {_fmt_utc(newest.ts_open) if newest else '-'}"
    )

    anchor = None
    if incremental:
        from pa_engine.records.history import build_anchor

        anchor = build_anchor(
            frame,
            symbol=data.symbol,
            timeframe=data.timeframe,
            records_dir=records_dir,
        )
        if anchor is None:
            print("增量: 无可用上次记录或没有新增已收盘K线，本次按全量分析")

    outcome = decide(frame, llm=_make_llm(llm_mode), vote=vote, anchor=anchor)
    code = _print_outcome(outcome, as_json)
    if save:
        _save_outcome(
            outcome,
            symbol=data.symbol,
            timeframe=data.timeframe,
            records_dir=records_dir,
            anchor_ts=float(newest.ts_open) if newest else None,
        )
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="re-pa-agent")
    parser.add_argument("--check", action="store_true", help="列出已注册闸门")
    parser.add_argument("--check-config", action="store_true", help="检查 JSON 配置必填项")
    parser.add_argument("--demo", action="store_true", help="在空帧上演示已注册闸门")
    parser.add_argument(
        "--live",
        action="store_true",
        help="抓取真实币安 K 线后跑决策（读 config/settings.json 的 data.*）",
    )
    parser.add_argument("--regime", choices=REGIMES, help="在合成行情上跑完整决策")
    parser.add_argument("--setup", action="store_true", help="叠加合格信号棒（验证下单路径）")
    parser.add_argument(
        "--llm",
        nargs="?",
        const="mock",
        choices=("mock", "http"),
        help="灰区感知层：mock=离线替身（默认）；http=按 config/settings.json 调用真实 API",
    )
    parser.add_argument("--json", action="store_true", help="输出完整决策 JSON")
    parser.add_argument(
        "--vote",
        type=int,
        default=0,
        metavar="N",
        help="灰区自一致性投票：对同一 prompt 采样 N 次取多数（默认 0=关闭，建议 3）",
    )
    parser.add_argument(
        "--save",
        dest="save",
        action="store_true",
        default=None,
        help="保存 v2 记录（--live 默认开启；--regime 默认关闭）",
    )
    parser.add_argument("--no-save", dest="save", action="store_false", help="不保存记录")
    parser.add_argument(
        "--records-dir",
        default=None,
        help="记录根目录（默认读 config/settings.json 的 records.dir）",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="读取同品种同周期上次记录，作为灰区提示词锚点（不影响代码判定）",
    )
    args = parser.parse_args(argv)
    if args.check:
        return _cmd_check()
    if args.check_config:
        return _cmd_check_config()
    if args.demo:
        return _cmd_demo()
    if args.live:
        return _cmd_live(
            llm_mode=args.llm or "",
            as_json=args.json,
            vote=max(0, args.vote),
            save=True if args.save is None else args.save,
            records_dir=args.records_dir,
            incremental=args.incremental,
        )
    if args.regime:
        return _cmd_regime(
            args.regime,
            llm_mode=args.llm or "",
            as_json=args.json,
            setup=args.setup,
            vote=max(0, args.vote),
            save=bool(args.save),
            records_dir=args.records_dir,
        )
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
