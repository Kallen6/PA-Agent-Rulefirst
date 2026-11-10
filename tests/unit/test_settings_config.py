"""JSON 配置测试（对齐 v1 `config/settings.json` 结构，无网络）。"""
from __future__ import annotations

import json
import sys
import tempfile
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pa_engine.ai.client import HttpGrayZoneClient
from pa_engine.config.paths import CONFIG_DIR
from pa_engine.config.settings import (
    ProviderSettings,
    Settings,
    load_settings,
    save_settings,
    validate_settings,
)


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_missing_explicit_path_returns_defaults() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "settings.json"
        settings = load_settings(path)
        assert settings.provider.api_key == ""
        assert settings.engine.llm_enabled is False
        assert settings.llm_ready is False
        assert path.exists() is False, "显式路径缺失时不应写盘"


def test_provider_fields_and_ready() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "settings.json"
        _write(
            path,
            {
                "provider": {
                    "model": "test-model",
                    "base_url": "http://127.0.0.1:9/v1",
                    "api_key": "sk-test",
                    "thinking": False,
                    "reasoning_effort": "high",
                    "context_window": 32000,
                    "fallbacks": [{"model": "backup", "api_key": "sk-backup"}],
                    "backup_glm_api_key": "sk-glm",
                },
                "engine": {"llm_enabled": True, "llm_timeout_seconds": 5},
            },
        )
        settings = load_settings(path)
    assert settings.provider.model == "test-model"
    assert settings.provider.api_key == "sk-test"
    assert settings.provider.thinking is False
    assert settings.provider.reasoning_effort == "high"
    assert settings.provider.context_window == 32000
    assert settings.provider.fallbacks[0]["model"] == "backup"
    assert settings.provider.backup_glm_api_key == "sk-glm"
    assert settings.engine.llm_timeout_seconds == 5
    assert settings.provider.ready is True
    assert settings.llm_ready is True


def test_api_key_encrypted_is_ignored_for_auth() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "settings.json"
        _write(
            path,
            {
                "provider": {
                    "api_key": "",
                    "api_key_encrypted": "BASE64-DPAPI-BLOB",
                    "base_url": "https://api.example.com/v1",
                },
                "engine": {"llm_enabled": True},
            },
        )
        settings = load_settings(path)
    assert settings.provider.api_key == ""
    assert settings.provider.ready is False
    assert settings.llm_ready is False, "v2 不解密 api_key_encrypted，不得视为已配置"


def test_unknown_fields_and_wrong_types_fall_back() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "settings.json"
        _write(
            path,
            {
                "provider": {"model": 123, "context_window": "bad", "unknown": "x"},
                "engine": {"llm_enabled": "yes", "llm_timeout_seconds": "soon"},
                "unknown_section": {"a": 1},
            },
        )
        settings = load_settings(path)
    assert settings.provider.model == "123"
    assert settings.provider.context_window == 128_000
    assert settings.engine.llm_enabled is False
    assert settings.engine.llm_timeout_seconds == 60


def test_malformed_json_returns_defaults_with_warning() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "settings.json"
        path.write_text("{not json", encoding="utf-8")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            settings = load_settings(path)
    assert settings.provider.api_key == ""
    assert any("读取失败" in str(item.message) for item in caught)


def test_client_from_settings_respects_switch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "settings.json"
        _write(
            path,
            {
                "provider": {
                    "base_url": "http://127.0.0.1:9/v1",
                    "api_key": "sk-test",
                },
                "engine": {"llm_enabled": False},
            },
        )
        settings = load_settings(path)
        disabled = HttpGrayZoneClient.from_settings(settings)
        assert disabled.enabled is False
        try:
            disabled.complete("x")
        except RuntimeError as exc:
            assert "未启用" in str(exc)
        else:
            raise AssertionError("未启用时必须拒绝调用")

        enabled_settings = Settings(
            provider=settings.provider,
            engine=settings.engine.__class__(llm_enabled=True),
        )
        enabled = HttpGrayZoneClient.from_settings(enabled_settings)
        assert enabled.enabled is True
        assert enabled.model == settings.provider.model


def test_records_and_v1_paths_loaded() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "settings.json"
        _write(path, {"records": {"dir": "data/records"}, "v1": {"path": "../V1"}})
        settings = load_settings(path)
    assert settings.records.dir == "data/records"
    assert settings.v1.path == "../V1"


def test_save_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "settings.json"
        original = Settings(
            provider=ProviderSettings(model="m", api_key="sk-roundtrip"),
            engine=Settings().engine,
        )
        save_settings(original, path)
        loaded = load_settings(path)
    assert loaded.provider.model == "m"
    assert loaded.provider.api_key == "sk-roundtrip"


def test_example_config_is_valid_and_key_empty() -> None:
    example = CONFIG_DIR / "settings.example.json"
    settings = load_settings(example)
    assert settings.provider.model
    assert settings.provider.api_key == ""
    assert settings.engine.llm_enabled is False
    assert settings.provider.api_key_encrypted == ""
    assert validate_settings(settings) == []


def test_binance_defaults_and_base_url() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "settings.json"
        _write(
            path,
            {
                "data": {
                    "source": "binance",
                    "symbol": "ETHUSDT",
                    "timeframe": "1h",
                    "bar_count": 300,
                    "market": "futures",
                }
            },
        )
        settings = load_settings(path)
    assert settings.data.symbol == "ETHUSDT"
    assert settings.data.timeframe == "1h"
    assert settings.data.bar_count == 300
    assert settings.data.resolved_base_url == "https://fapi.binance.com"

    spot = Settings.from_dict({"data": {"market": "spot"}})
    assert spot.data.resolved_base_url == "https://api.binance.com"

    proxy = Settings.from_dict({"data": {"base_url": "http://127.0.0.1:8080/"}})
    assert proxy.data.resolved_base_url == "http://127.0.0.1:8080"


def test_validate_settings_flags_bad_binance_fields() -> None:
    valid = Settings.from_dict(
        {"data": {"source": "binance", "symbol": "BTCUSDT", "timeframe": "15m"}}
    )
    assert validate_settings(valid) == []

    broken = Settings.from_dict(
        {
            "data": {
                "source": "binance",
                "symbol": "",
                "timeframe": "2m",
                "bar_count": 2000,
                "market": "margin",
            }
        }
    )
    issues = validate_settings(broken)
    assert any("data.symbol" in item for item in issues)
    assert any("data.timeframe" in item for item in issues)
    assert any("data.bar_count" in item for item in issues)
    assert any("data.market" in item for item in issues)


def test_validate_settings_requires_llm_key_when_enabled() -> None:
    settings = Settings.from_dict(
        {
            "provider": {"base_url": "https://api.example.com/v1", "api_key": ""},
            "engine": {"llm_enabled": True},
        }
    )
    issues = validate_settings(settings)
    assert any("provider.api_key" in item for item in issues)


def test_settings_json_marks_required_items_and_is_standalone() -> None:
    raw = json.loads((CONFIG_DIR / "settings.json").read_text(encoding="utf-8"))
    required = raw["_configure"]["required"]
    assert "data.source" in required
    assert "data.symbol" in required
    assert "data.timeframe" in required
    assert raw["data"]["source"] == "binance"
    assert "v1" not in raw, "独立项目配置不应包含 v1 路径"
    example_raw = json.loads(
        (CONFIG_DIR / "settings.example.json").read_text(encoding="utf-8")
    )
    assert "v1" not in example_raw
