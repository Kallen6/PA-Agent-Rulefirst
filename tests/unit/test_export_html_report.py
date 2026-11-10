"""HTML 报告导出：图表渲染、转义、无 K 线降级、文件落盘。"""
from __future__ import annotations

import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from pa_engine.engine.pipeline import decide
from tests.fixtures.synthetic import make_regime_rows, make_setup_rows
from tests.support import build_v2_frame
from tools.export_html_report import _case_record, main, render_html


def _setup_case():
    frame = build_v2_frame(make_setup_rows("tight_channel"))
    return frame, decide(frame)


def test_html_contains_chart_cards_and_trace() -> None:
    frame, outcome = _setup_case()
    markup = render_html(frame, outcome, source="unit")
    assert "<!DOCTYPE html>" in markup
    assert '<svg viewBox="0 0 1040 470"' in markup
    assert markup.count("<rect") > 50          # 100 根 K 线实体
    assert 'class="cards"' in markup
    assert "阶段一：结构与状态" in markup
    assert "阶段二：入场、风险、下单" in markup
    assert "灰区与感知层" in markup


def test_signal_bar_is_highlighted() -> None:
    frame, outcome = _setup_case()
    markup = render_html(frame, outcome, source="unit")
    assert "#facc15" in markup                 # 信号棒底色
    assert "信号棒" in markup


def test_dynamic_text_is_escaped() -> None:
    frame, outcome = _setup_case()
    outcome.decision["reason"] = '<script>alert("x")</script>'
    markup = render_html(frame, outcome, source="unit")
    assert "<script>alert" not in markup
    assert "&lt;script&gt;" in markup


def test_render_degrades_without_bars() -> None:
    frame, outcome = _setup_case()
    with tempfile.TemporaryDirectory() as tmp:
        bare_frame, bare_outcome, source = _case_record(
            _write_record(outcome.decision, tmp)
        )
    markup = render_html(bare_frame, bare_outcome, source=source)
    assert "无法绘图" in markup
    assert "<svg" not in markup
    assert bare_frame.bars == ()
    assert bare_outcome.decision["cycle_position"] == outcome.decision["cycle_position"]


def test_main_writes_html_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "report.html"
        with redirect_stdout(io.StringIO()):
            code = main(
                [
                    "--regime",
                    "broad_channel",
                    "--setup",
                    "--out",
                    str(out),
                ]
            )
        assert code == 0
        assert out.is_file()
        text = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in text
        assert "background_limit" in text or "signal" in text


def _write_record(decision: dict, directory: str | Path) -> Path:
    """把 decision 包成最小 v2 记录，用于验证无 K 线降级。"""
    payload = {
        "schema_version": "v2",
        "symbol": "TEST",
        "timeframe": "1h",
        "stage1": {"cycle_position": decision.get("cycle_position")},
        "decision": decision,
        "gray_zone": {"need_llm": False, "contested": [], "reasons": [], "llm_called": False},
        "prompt_stats": {},
        "llm_called": False,
        "llm_answer": None,
    }
    path = Path(directory) / "record.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path
