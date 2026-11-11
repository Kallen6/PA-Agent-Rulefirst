"""Web 决策台单测：API 编排 / 历史读写 / 路径越界 / HTTP 集成 / 渲染回归。"""
from __future__ import annotations

import json
import tempfile
import threading
import urllib.request
from pathlib import Path

from pa_engine.web.server import (
    REGIMES,
    _list_records,
    _load_record,
    _run_decide,
    create_server,
)


def _decide_body(**overrides) -> dict:
    body = {"source": "regime", "regime": "broad_channel", "save": False}
    body.update(overrides)
    return body


def test_regime_decide_returns_report_without_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_decide(_decide_body(), records_dir=tmp)
    assert result["ok"] is True
    assert result["report_html"].startswith("<!DOCTYPE html>")
    assert "<svg" in result["report_html"]
    assert result["saved_path"] is None
    summary = result["summary"]
    assert summary["source"].startswith("synthetic:")
    assert summary["terminal"]["outcome"] in ("trade", "wait", "reject")


def test_all_regimes_smoke() -> None:
    for regime in REGIMES:
        result = _run_decide(_decide_body(regime=regime), records_dir=None)
        assert result["ok"] is True, regime
        assert result["summary"]["terminal"]["outcome"], regime


def test_save_flow_and_history_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_decide(_decide_body(save=True), records_dir=tmp)
        assert result["ok"] is True
        assert result["saved_path"] is not None
        assert Path(result["saved_path"]).is_file()

        listed = _list_records(records_dir=tmp, limit=10)
        assert listed["ok"] is True
        assert len(listed["records"]) == 1
        item = listed["records"][0]
        assert item["outcome"] in ("trade", "wait", "reject")

        loaded = _load_record({"path": [result["saved_path"]]}, records_dir=tmp)
        assert loaded["ok"] is True
        assert loaded["report_html"].startswith("<!DOCTYPE html>")


def test_record_path_traversal_blocked() -> None:
    try:
        _load_record({"path": ["../../pa_engine/main.py"]}, records_dir=None)
    except PermissionError:
        return
    raise AssertionError("越界路径未被拦截")


def test_band_validation() -> None:
    try:
        _run_decide(_decide_body(band=1.0), records_dir=None)
    except ValueError:
        return
    raise AssertionError("带宽 1.0 未被拒绝")


def test_http_integration() -> None:
    httpd = create_server(host="127.0.0.1", port=0, records_dir=None)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        base = f"http://{host}:{port}"

        # 首页与静态资源
        with urllib.request.urlopen(f"{base}/", timeout=5) as res:
            assert res.status == 200
            assert "PA 决策台" in res.read().decode("utf-8")
        with urllib.request.urlopen(f"{base}/app.js", timeout=5) as res:
            assert res.status == 200
        with urllib.request.urlopen(f"{base}/style.css", timeout=5) as res:
            assert res.status == 200

        # POST /api/decide
        request = urllib.request.Request(
            f"{base}/api/decide",
            data=json.dumps(_decide_body()).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as res:
            data = json.loads(res.read().decode("utf-8"))
        assert data["ok"] is True
        assert data["report_html"].startswith("<!DOCTYPE html>")

        # 未知路径 → 404
        try:
            urllib.request.urlopen(f"{base}/nope", timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("未知路径未返回 404")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_render_html_contains_core_sections() -> None:
    result = _run_decide(_decide_body(regime="extreme_tr"), records_dir=None)
    html = result["report_html"]
    for marker in ("阶段一：结构与状态", "阶段二：入场、风险、下单", "灰区与感知层", "badge-wait"):
        assert marker in html, marker
