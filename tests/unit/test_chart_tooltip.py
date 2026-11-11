"""K 线悬停 tooltip 渲染单测。"""
from __future__ import annotations

from pa_engine.engine.pipeline import DecisionOutcome, decide
from pa_engine.report import render_html
from tests.fixtures.synthetic import make_regime_rows
from tests.support import build_v2_frame


def _report(regime: str = "broad_channel") -> str:
    frame = build_v2_frame(make_regime_rows(regime))
    return render_html(frame, decide(frame), source="test")


def test_hit_columns_match_bar_count() -> None:
    html = _report()
    bars = len(build_v2_frame(make_regime_rows("broad_channel")).bars)
    assert html.count('class="hit"') == bars


def test_hit_columns_carry_full_bar_data() -> None:
    html = _report()
    for marker in ("data-o=", "data-h=", "data-l=", "data-c=", "data-v=", "data-ema=", "data-t=", "data-dir="):
        assert marker in html, marker


def test_tooltip_container_and_script_present() -> None:
    html = _report()
    assert 'id="kbar-tip"' in html
    assert "kbar-tip" in html and "<script>" in html
    assert "closest('.hit')" in html


def test_no_hit_columns_without_bars() -> None:
    """无 K 线（记录回放）时不渲染命中列，图表区给出降级提示。"""
    frame = type("_F", (), {"symbol": "T", "timeframe": "1h", "bars": (), "indicators": None})()
    decision = {"terminal": {"node_id": "9.0", "outcome": "wait", "label": "t"}}
    outcome = DecisionOutcome(decision=decision)
    html = render_html(frame, outcome, source="record:test")
    assert 'class="hit"' not in html
    assert "该记录未保存 K 线数据" in html
