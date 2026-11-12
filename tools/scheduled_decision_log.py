"""定时决策记录脚本：每 30 分钟拉取 ETHUSDT K 线并决策一次，结果追加写入 CSV。

用法：
    python tools/scheduled_decision_log.py                # 常驻循环（对齐 :00/:30 边界）
    python tools/scheduled_decision_log.py --once         # 只跑一轮（测试用）
    python tools/scheduled_decision_log.py --timeframe 15m --interval-min 30
    python tools/scheduled_decision_log.py --base-url https://data-api.binance.vision
    python tools/scheduled_decision_log.py --proxy http://127.0.0.1:7890
    python tools/scheduled_decision_log.py --stance conservative
                                          临时指定档位（默认 aggressive＝激进策略）
    python tools/scheduled_decision_log.py --once --out records/decision_log/eth_aggressive.csv
                                          指定输出 CSV（缺省按 品种_周期_档位 命名）

说明：
- 仅标准库（csv/argparse/time），复用 pa_engine 的抓取与决策链路；
- 单轮失败自动重试（默认最多 3 次尝试，间隔 5s/15s/30s），仍失败才写 error 行；
- CSV 表头与枚举值使用中文（中英映射见 HEADER_ZH 与 collect_fields），UTF-8-BOM，
  Excel 直接打开不乱码；
- 按最新已收盘 K 线时间去重：同一根 K 线重复执行不会产生重复行（兼容旧英文表头文件）；
- 网络不可达时用 --base-url（镜像）或 --proxy（本地代理），与 export_html_report 口径一致。
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pa_engine.config.thresholds import Stance  # noqa: E402
from pa_engine.engine.pipeline import DecisionOutcome, decide  # noqa: E402
from pa_engine.report import _zh as _zh_term  # noqa: E402  # 复用渲染层的中英术语映射

# 内部键（顺序即 CSV 列顺序）
FIELDNAMES = (
    "run_ts",                 # 本地运行时间
    "bar_time_utc",           # 最新已收盘 K 线时间（去重键）
    "symbol",
    "timeframe",
    "decision_stance",        # 下单倾向档位（v2 档位，含生效 RR 门槛）
    "current_strategy",       # 当前策略（档位策略名，便于按策略筛选）
    "cycle_position",         # 周期定位
    "direction",              # 方向
    "always_in",              # 在场方向
    "diagnosis_confidence",   # 诊断置信 %
    "gate_result",            # 闸门结论
    "terminal_node",          # 终态节点
    "terminal_outcome",       # 终态
    "terminal_label",         # 终态说明
    "order_type",             # 委托方式
    "entry_style",            # 入场路径
    "entry_price",            # 入场价
    "stop_loss_price",        # 止损
    "take_profit_price",      # TP1
    "take_profit_price_2",    # TP2
    "rr",                     # RR
    "estimated_win_rate",     # 估算胜率 %
    "spike_stage",            # 尖峰阶段
    "zone",                   # 区间位置
    "breakout_quality",       # 突破质量
    "gray_need_llm",          # 灰区建议感知
    "gray_contested",         # 灰区争议节点
    "llm_called",             # 感知层已调用
    "elapsed_ms",             # 本轮抓取+决策耗时（毫秒，成功尝试）
    "error",                  # 异常信息
)

# 中文表头（中英映射；专有名词保留英文更清晰）
HEADER_ZH: dict[str, str] = {
    "run_ts": "运行时间",
    "bar_time_utc": "K线时间(UTC)",
    "symbol": "交易对",
    "timeframe": "周期",
    "decision_stance": "下单倾向",
    "current_strategy": "当前策略",
    "cycle_position": "周期定位",
    "direction": "方向",
    "always_in": "在场方向",
    "diagnosis_confidence": "诊断置信%",
    "gate_result": "闸门结论",
    "terminal_node": "终态节点",
    "terminal_outcome": "终态",
    "terminal_label": "终态说明",
    "order_type": "委托方式",
    "entry_style": "入场路径",
    "entry_price": "入场价",
    "stop_loss_price": "止损",
    "take_profit_price": "TP1",
    "take_profit_price_2": "TP2",
    "rr": "RR",
    "estimated_win_rate": "估算胜率%",
    "spike_stage": "尖峰阶段",
    "zone": "区间位置",
    "breakout_quality": "突破质量",
    "gray_need_llm": "灰区建议感知",
    "gray_contested": "灰区争议节点",
    "llm_called": "感知层已调用",
    "elapsed_ms": "耗时(ms)",
    "error": "异常信息",
}

# 去重键在中文/英文表头下的列名（兼容旧文件）
BAR_TIME_HEADERS = ("K线时间(UTC)", "bar_time_utc")

RETRY_ATTEMPTS = 3
RETRY_WAIT_SECONDS = (5, 15, 30)

EMPTY_ROW: dict[str, str] = {key: "" for key in FIELDNAMES}


# ── 纯函数：字段收集与 CSV 写入（可离线单测） ──────────────────────────────


def _bar_time_utc(frame) -> str:
    bars = getattr(frame, "bars", ()) or ()
    if not bars:
        return ""
    try:
        moment = datetime.fromtimestamp(float(bars[0].ts_open) / 1000, timezone.utc)
        return moment.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _yn(value) -> str:
    """布尔 → 是/否（空值原样为空）。"""
    if value is None or value == "":
        return ""
    return "是" if value else "否"


def stance_columns(stance) -> tuple[str, str]:
    """档位（decision 的 stance 字典或名称）→ (下单倾向, 当前策略) 两列文本。

    统一走 `policy_of`，保证正常行与 error 行格式一致，且两列都记录档位。
    """
    from pa_engine.engine.stance import policy_of

    name = stance.get("name") if isinstance(stance, dict) else stance
    policy = policy_of(str(name or ""))
    return (
        f"{policy.label_zh}（RR≥{policy.min_rr:.1f}）",
        f"{policy.label_zh}策略",
    )


def collect_fields(frame, outcome: DecisionOutcome) -> dict[str, str]:
    """决策结果 → 一行 CSV 字段（枚举值经 ZH_TERMS 映射为中文，便于 Excel 阅读）。"""
    decision = outcome.decision
    stage1 = outcome.stage1 or {}
    terminal = decision.get("terminal") or {}
    rr = ""
    for result in outcome.gate_results:
        if result.node_id == "10.2":
            value = (result.evidence or {}).get("rr")
            if value is not None:
                rr = f"{float(value):.2f}"
            break
    gray = outcome.gray_zone
    # 「下单倾向」记档位+门槛；「当前策略」单独成列，便于 Excel 里按策略筛选/透视
    stance_text, strategy_text = stance_columns(decision.get("stance"))
    row = dict(EMPTY_ROW)
    row.update(
        {
            "bar_time_utc": _bar_time_utc(frame),
            "symbol": str(getattr(frame, "symbol", "")),
            "timeframe": str(getattr(frame, "timeframe", "")),
            "decision_stance": stance_text,
            "current_strategy": strategy_text,
            "cycle_position": _zh_term(decision.get("cycle_position") or ""),
            "direction": _zh_term(decision.get("direction") or ""),
            "always_in": _zh_term(decision.get("always_in") or ""),
            "diagnosis_confidence": (
                # 决策记录里已是 0-100 百分数（pipeline._diagnosis_confidence），勿再 ×100
                f"{float(decision['diagnosis_confidence']):.0f}%"
                if decision.get("diagnosis_confidence") is not None
                else ""
            ),
            "gate_result": _zh_term(decision.get("gate_result") or ""),
            "terminal_node": str(terminal.get("node_id") or ""),
            "terminal_outcome": _zh_term(terminal.get("outcome") or ""),
            "terminal_label": str(terminal.get("label") or ""),
            "order_type": _zh_term(decision.get("order_type") or ""),
            "entry_style": _zh_term(decision.get("entry_style") or ""),
            "entry_price": _px(decision.get("entry_price")),
            "stop_loss_price": _px(decision.get("stop_loss_price")),
            "take_profit_price": _px(decision.get("take_profit_price")),
            "take_profit_price_2": _px(decision.get("take_profit_price_2")),
            "rr": rr,
            "estimated_win_rate": (
                f"{float(decision['estimated_win_rate']):.0f}%"
                if decision.get("estimated_win_rate")
                else ""
            ),
            "spike_stage": _zh_term(stage1.get("spike_stage") or ""),
            "zone": _zh_term(stage1.get("zone") or ""),
            "breakout_quality": _zh_term(stage1.get("breakout_quality") or ""),
            "gray_need_llm": _yn(gray.need_llm) if gray else "",
            "gray_contested": " ".join(gray.contested_nodes) if gray else "",
            "llm_called": _yn(outcome.llm_called),
        }
    )
    return row


def _px(value) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    magnitude = abs(number)
    if magnitude >= 1000:
        return f"{number:.2f}"
    if magnitude >= 1:
        return f"{number:.4f}".rstrip("0").rstrip(".")
    return f"{number:.6f}".rstrip("0").rstrip(".")


def init_csv(path: Path) -> bool:
    """确保 CSV 存在并带当前中文表头；返回是否新建（含补写空文件表头）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size > 0:
        return False
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        csv.writer(handle).writerow([HEADER_ZH[key] for key in FIELDNAMES])
    return True


def append_csv(path: Path, row: dict[str, str]) -> None:
    """追加一行；新文件写中文表头（顺序与 FIELDNAMES 一致）。

    已存在的旧文件按**文件实际表头**对齐列序：新增档位列不会让历史 CSV 错位，
    缺失列补空、已删除列忽略。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    header: list[str] = []
    if path.is_file():
        try:
            with path.open("r", newline="", encoding="utf-8-sig") as handle:
                header = next(csv.reader(handle), None) or []
        except OSError:
            header = []
    with path.open("a", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        if not header:
            writer.writerow([HEADER_ZH[key] for key in FIELDNAMES])
            writer.writerow([row[key] for key in FIELDNAMES])
            return
        label_to_key = {HEADER_ZH[key]: key for key in FIELDNAMES}
        keys = [label_to_key.get(name) for name in header]
        if all(keys):
            writer.writerow([row.get(key, "") for key in keys])
        else:  # 表头完全无法识别（被手工改过）：退回当前列序
            writer.writerow([row[key] for key in FIELDNAMES])


def already_logged(path: Path, bar_time_utc: str) -> bool:
    """按「K线时间(UTC)」列去重；兼容旧英文表头文件。"""
    if not bar_time_utc or not path.is_file():
        return False
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader, None) or []
        if not header:
            return False
        idx = next(
            (header.index(name) for name in BAR_TIME_HEADERS if name in header),
            None,
        )
        if idx is None:
            return False
        return any(len(row) > idx and row[idx] == bar_time_utc for row in reader)


# ── 数据编排 ────────────────────────────────────────────────────────────────


def _make_llm(mode: str):
    if not mode:
        return None
    if mode == "mock":
        from pa_engine.ai.client import MockLLM

        return MockLLM()
    from pa_engine.ai.client import HttpGrayZoneClient

    return HttpGrayZoneClient.from_settings()


def resolve_stance(args: argparse.Namespace) -> str:
    """脚本档位：`--stance` 优先（CLI 默认 aggressive），未传时读配置文件。"""
    from pa_engine.config.settings import load_settings

    return getattr(args, "stance", None) or load_settings().engine.decision_stance


def _fetch_and_decide(args: argparse.Namespace):
    """拉取 K 线并决策（可抛异常，由 run_once 统一重试/落盘）。"""
    from dataclasses import replace as dc_replace

    from pa_engine.config.settings import load_settings
    from pa_engine.data.binance import load_binance_frame

    data = load_settings().data
    stance = resolve_stance(args)
    data = dc_replace(
        data,
        symbol=args.symbol.upper(),
        timeframe=args.timeframe,
        bar_count=max(20, min(1000, args.bars)),
    )
    if args.base_url:
        data = dc_replace(data, base_url=args.base_url)
    if args.market in ("spot", "futures"):
        data = dc_replace(data, market=args.market)

    opener = None
    if args.proxy:
        import urllib.request

        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": args.proxy, "https": args.proxy})
        ).open

    frame = load_binance_frame(data, opener=opener)
    return frame, decide(
        frame, llm=_make_llm(args.llm), vote=args.vote, stance=stance
    )


def run_once(args: argparse.Namespace, out_path: Path) -> dict[str, str]:
    """拉取 → 决策 → 追加 CSV；失败自动重试（最多 RETRY_ATTEMPTS 次）。"""
    run_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_started = time.perf_counter()
    last_exc: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        started = time.perf_counter()
        try:
            frame, outcome = _fetch_and_decide(args)
            row = collect_fields(frame, outcome)
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            row["run_ts"] = run_ts
            row["elapsed_ms"] = str(elapsed_ms)

            if already_logged(out_path, row["bar_time_utc"]):
                print(f"[skip] {row['bar_time_utc']} 已记录，跳过（{row['terminal_outcome']}）")
                return {}
            append_csv(out_path, row)
            print(
                f"[ok]   {run_ts} | {row['symbol']} {row['timeframe']} K线@{row['bar_time_utc']} | "
                f"{row['cycle_position']} {row['direction']} | "
                f"终态 §{row['terminal_node']} {row['terminal_outcome']} | {row['order_type']} | "
                f"耗时 {elapsed_ms}ms"
                + (f"（第 {attempt} 次尝试成功）" if attempt > 1 else "")
            )
            return row
        except Exception as exc:  # 网络/数据异常：退避后重试
            last_exc = exc
            if attempt < RETRY_ATTEMPTS:
                wait = RETRY_WAIT_SECONDS[attempt - 1]
                print(f"[retry] 第 {attempt}/{RETRY_ATTEMPTS} 次失败：{exc} → {wait}s 后重试")
                time.sleep(wait)
    # 全部尝试失败：写一行 error 记录后返回，不中断常驻循环（耗时含重试等待）
    stance_text, strategy_text = stance_columns(resolve_stance(args))
    row = dict(EMPTY_ROW)
    row.update(
        {
            "run_ts": run_ts,
            "symbol": args.symbol.upper(),
            "timeframe": args.timeframe,
            "decision_stance": stance_text,
            "current_strategy": strategy_text,
            "elapsed_ms": str(round((time.perf_counter() - run_started) * 1000)),
            "error": f"{type(last_exc).__name__}: {last_exc}",
        }
    )
    append_csv(out_path, row)
    print(f"[err]  {run_ts} | {RETRY_ATTEMPTS} 次尝试均失败 | {type(last_exc).__name__}: {last_exc}")
    return row


def next_boundary(interval_min: int) -> float:
    """下一个 :00/:30 类对齐边界的 epoch 秒（恰好落在边界上则立即执行）。"""
    step = interval_min * 60
    now = time.time()
    return now if now % step == 0 else (now // step + 1) * step


# ── CLI ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="定时拉取 K 线并决策，结果追加 CSV（零依赖）")
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--interval-min", type=int, default=15, help="执行间隔分钟（默认 15）")
    parser.add_argument("--bars", type=int, default=101, help="每次拉取 K 线根数")
    parser.add_argument("--market", default="spot", choices=("spot", "futures"))
    parser.add_argument(
        "--stance",
        choices=Stance.ORDER,
        default="aggressive",
        help=(
            "下单倾向档位（本脚本默认 aggressive＝激进策略；"
            "传 conservative/balanced/extreme_aggressive 可覆盖）"
        ),
    )
    parser.add_argument("--base-url", default=None, help="镜像域名，如 https://data-api.binance.vision")
    parser.add_argument("--proxy", default=None, help="HTTP(S) 代理，如 http://127.0.0.1:7890")
    parser.add_argument("--llm", nargs="?", const="mock", default="", help="mock / http，默认不调用")
    parser.add_argument("--vote", type=int, default=0, help="灰区自一致性投票次数")
    parser.add_argument("--out", default=None, help="CSV 路径（默认 records/decision_log/）")
    parser.add_argument("--once", action="store_true", help="只执行一轮后退出")
    args = parser.parse_args(argv)

    safe_symbol = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in args.symbol)
    out_path = (
        Path(args.out)
        if args.out
        else ROOT
        / "records"
        / "decision_log"
        / f"{safe_symbol}_{args.timeframe}_{args.stance}.csv"
    )
    created = init_csv(out_path)
    print(
        f"定时决策记录：{args.symbol} {args.timeframe} 每 {args.interval_min} 分钟 "
        f"策略={args.stance} → {out_path}"
    )
    if created:
        print("已新建 CSV 并写入表头（含「下单倾向」「当前策略」列）")

    if args.once:
        run_once(args, out_path)
        return 0

    print("Ctrl+C 停止。")
    try:
        while True:
            target = next_boundary(args.interval_min)
            wait = target - time.time()
            if wait > 1.5:
                print(
                    f"[wait] 下次执行 "
                    f"{datetime.fromtimestamp(target).strftime('%Y-%m-%d %H:%M:%S')}"
                    f"（{wait / 60:.1f} 分钟后）"
                )
                # 分段长睡眠到边界前 1.5s（避免长 sleep 漂移，Ctrl+C 也能及时响应）
                while True:
                    remaining = target - time.time()
                    if remaining <= 1.5:
                        break
                    time.sleep(min(remaining - 1.5, 30))
            # 边界前最后 ~1.5s：短间隔轮询到点，把唤醒误差压到 0.1s 以内
            while time.time() < target:
                time.sleep(min(target - time.time(), 0.05))
            delay = time.time() - target
            if delay >= 1.0:
                print(
                    f"[late] 比计划晚 {delay:.1f}s 执行——"
                    "多为系统睡眠唤醒或高负载；挂机运行建议关闭自动睡眠"
                )
            run_once(args, out_path)
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
