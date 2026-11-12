"""定时决策记录脚本单测（离线：合成行情 + 临时 CSV + 重试注入）。"""
from __future__ import annotations

import argparse
import csv
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pa_engine.data.binance as binance_mod
from pa_engine.engine.pipeline import decide
from tests.fixtures.synthetic import make_regime_rows
from tests.support import build_v2_frame
from tools.scheduled_decision_log import (
    FIELDNAMES,
    HEADER_ZH,
    RETRY_ATTEMPTS,
    RETRY_WAIT_SECONDS,
    already_logged,
    append_csv,
    collect_fields,
    next_boundary,
    run_once,
)


def _outcome(regime: str = "broad_channel"):
    frame = build_v2_frame(make_regime_rows(regime))
    return frame, decide(frame)


def test_header_mapping_complete_and_chinese() -> None:
    assert set(HEADER_ZH.keys()) == set(FIELDNAMES)
    for key in ("run_ts", "terminal_outcome", "error", "current_strategy"):
        assert HEADER_ZH[key]
    assert HEADER_ZH["decision_stance"] == "下单倾向"
    assert HEADER_ZH["current_strategy"] == "当前策略"


def test_init_csv_writes_header_once() -> None:
    from tools.scheduled_decision_log import init_csv

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "new.csv"
        assert init_csv(path) is True
        assert init_csv(path) is False  # 已有表头不重复写
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        assert len(rows) == 1
        assert rows[0] == [HEADER_ZH[key] for key in FIELDNAMES]
        assert "当前策略" in rows[0]


def test_collect_fields_uses_chinese_values() -> None:
    frame, outcome = _outcome()
    row = collect_fields(frame, outcome)
    # 枚举值经 ZH_TERMS 映射为中文
    assert row["cycle_position"] == "宽通道"
    assert row["direction"] in ("多头", "空头")
    assert row["terminal_outcome"] in ("可交易", "观望", "否决")
    assert row["gate_result"] in ("放行", "观望", "否决", "未知")
    assert row["zone"] != "" and row["breakout_quality"] != ""
    assert row["bar_time_utc"] != ""
    # 量纲防回归：两个字段在决策记录中均为 0-100 百分数，CSV 值不得超 100
    for key in ("diagnosis_confidence", "estimated_win_rate"):
        if row[key]:
            assert float(row[key].rstrip("%")) <= 100, (key, row[key])
    # trade 场景价格字段应齐
    if row["terminal_outcome"] == "可交易":
        assert row["entry_price"] and row["stop_loss_price"] and row["take_profit_price"]
        assert row["rr"] != "" and row["estimated_win_rate"] != ""


def test_csv_append_chinese_header_once() -> None:
    frame, outcome = _outcome()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "log.csv"
        row = collect_fields(frame, outcome)
        append_csv(path, row)
        append_csv(path, dict(row, run_ts="second"))
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        assert len(rows) == 3  # 中文表头 + 2 行
        assert rows[0][0] == "运行时间"
        assert rows[0].index("K线时间(UTC)") == FIELDNAMES.index("bar_time_utc")
        assert len(rows[1]) == len(FIELDNAMES)
        assert rows[2][0] == "second"


def test_dedupe_by_bar_time_chinese_header() -> None:
    frame, outcome = _outcome()
    row = collect_fields(frame, outcome)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "log.csv"
        assert already_logged(path, row["bar_time_utc"]) is False
        append_csv(path, row)
        assert already_logged(path, row["bar_time_utc"]) is True
        assert already_logged(path, "2000-01-01 00:00") is False


def test_dedupe_legacy_english_header() -> None:
    """旧版英文表头文件同样能去重。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "legacy.csv"
        path.write_text("run_ts,bar_time_utc,symbol\nold,2026-11-11 09:00,ETHUSDT\n", encoding="utf-8-sig")
        assert already_logged(path, "2026-11-11 09:00") is True
        assert already_logged(path, "2026-11-11 09:15") is False


def test_next_boundary_alignment() -> None:
    now = time.time()
    target = next_boundary(30)
    assert target >= now
    aligned = datetime.fromtimestamp(target).minute
    assert aligned in (0, 30) or (target - now) == 0 or (target - now) <= 30 * 60


def test_run_once_retries_then_succeeds() -> None:
    """首次失败 → 重试成功：只写一行正常记录，不写 error 行。"""
    import tools.scheduled_decision_log as sched

    frame, _ = _outcome()
    calls = {"n": 0}
    original_load = binance_mod.load_binance_frame
    original_wait = sched.RETRY_WAIT_SECONDS

    def flaky(data, opener=None):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("网络不可达：timed out")
        return frame

    binance_mod.load_binance_frame = flaky
    sched.RETRY_WAIT_SECONDS = (0, 0, 0)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "r.csv"
            args = argparse.Namespace(
                symbol="ethusdt", timeframe="15m", bars=101, market="spot",
                base_url=None, proxy=None, llm="", vote=0,
            )
            row = run_once(args, out)
            assert calls["n"] == 2
            assert row.get("terminal_outcome") != ""
            # 耗时列：成功尝试须为非负整数毫秒
            assert row.get("elapsed_ms") != "" and int(row["elapsed_ms"]) >= 0
            lines = out.read_text(encoding="utf-8-sig").strip().splitlines()
            assert len(lines) == 2  # 表头 + 1 行，无 error 行
    finally:
        binance_mod.load_binance_frame = original_load
        sched.RETRY_WAIT_SECONDS = original_wait


def test_run_once_all_retries_fail_writes_error_row() -> None:
    """连续失败 → 重试满 3 次后写一行 error 记录。"""
    import tools.scheduled_decision_log as sched

    calls = {"n": 0}
    original_load = binance_mod.load_binance_frame
    original_wait = sched.RETRY_WAIT_SECONDS

    def always_fail(data, opener=None):
        calls["n"] += 1
        raise RuntimeError("网络不可达：[WinError 10054]")

    binance_mod.load_binance_frame = always_fail
    sched.RETRY_WAIT_SECONDS = (0, 0, 0)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "r.csv"
            args = argparse.Namespace(
                symbol="ETHUSDT", timeframe="15m", bars=101, market="spot",
                base_url=None, proxy=None, llm="", vote=0,
            )
            row = run_once(args, out)
            assert calls["n"] == RETRY_ATTEMPTS
            assert "WinError 10054" in row["error"]
            # error 行也要带档位，便于按策略筛选时不丢归属
            assert row["decision_stance"] != "" and row["current_strategy"] != ""
            # error 行的耗时含全部重试等待，非空非负
            assert row.get("elapsed_ms") != "" and int(row["elapsed_ms"]) >= 0
            lines = out.read_text(encoding="utf-8-sig").strip().splitlines()
            assert len(lines) == 2  # 表头 + 1 行 error
    finally:
        binance_mod.load_binance_frame = original_load
        sched.RETRY_WAIT_SECONDS = original_wait
