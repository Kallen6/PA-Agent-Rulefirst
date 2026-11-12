"""JSON 配置（对齐 v1 `config/settings.json` 的 `provider` 结构）。

与 v1 的关系：
- 顶层 `provider` 字段名/语义一致：model / base_url / api_key / thinking /
  reasoning_effort / context_window / fallbacks / backup_glm_api_key；
- `api_key_encrypted` 仅作 v1 遗留字段保留，**v2 不实现 DPAPI 解密**，
  鉴权只认 `provider.api_key`；
- 读取时忽略未知字段（等价 v1 pydantic 的 `extra="ignore"`）。

v2 新增段：
- `engine`：是否允许灰区真实调用、超时、温度；
- `records`：记录落盘目录；
- `v1`：v1 只读参照路径；
- `data` / `notify`：真实行情与通知的缺口配置（当前未接线）。

`_configure` 段是 JSON 的注释位（JSON 无行注释语法）：必填项清单、字段说明，
以及 `gaps` 中各缺口运行所需配置，全部集中在此；本模块加载时整段忽略。

安全说明：`provider.api_key` 为明文落盘（与 v1 现行实现一致），
`config/settings.json` 已加入 .gitignore；日志/记录不得输出该字段。
"""
from __future__ import annotations

import json
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from pa_engine.config.paths import SETTINGS_JSON_PATH
from pa_engine.config.thresholds import Stance


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _as_bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ProviderSettings:
    """AI provider（字段与 v1 `AIProviderSettings` 对齐）。"""

    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    api_key_encrypted: str = ""  # v1 遗留；v2 不解析
    thinking: bool = True
    reasoning_effort: str = "low"
    context_window: int = 128_000
    fallbacks: tuple[dict[str, Any], ...] = ()
    backup_glm_api_key: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "ProviderSettings":
        data = _as_dict(raw)
        fallbacks_raw = data.get("fallbacks") or []
        fallbacks = (
            tuple(dict(item) for item in fallbacks_raw if isinstance(item, Mapping))
            if isinstance(fallbacks_raw, (list, tuple))
            else ()
        )
        return cls(
            model=_as_str(data.get("model"), cls.model),
            base_url=_as_str(data.get("base_url"), cls.base_url),
            api_key=_as_str(data.get("api_key")),
            api_key_encrypted=_as_str(data.get("api_key_encrypted")),
            thinking=_as_bool(data.get("thinking"), cls.thinking),
            reasoning_effort=_as_str(data.get("reasoning_effort"), cls.reasoning_effort),
            context_window=_as_int(data.get("context_window"), cls.context_window),
            fallbacks=fallbacks,
            backup_glm_api_key=_as_str(data.get("backup_glm_api_key")),
        )

    @property
    def ready(self) -> bool:
        """有 key 且有 base_url 才具备调用条件。"""
        return bool(self.api_key and self.base_url)


def _normalize_stance_name(value: Any) -> str:
    """配置容错：接受英文档位名或中文标签，其余一律回落默认档。"""
    text = str(value or "").strip()
    if not text:
        return Stance.DEFAULT
    if text in Stance.ORDER:
        return text
    for name, label in Stance.LABELS_ZH.items():
        if text == label:
            return name
    return Stance.DEFAULT


@dataclass(frozen=True)
class EngineSettings:
    """v2 引擎开关。阈值仍以 `thresholds.py` 为唯一来源。"""

    llm_enabled: bool = False
    llm_timeout_seconds: int = 60
    llm_temperature: float = 0.0
    #: 下单倾向档位：conservative / balanced / aggressive / extreme_aggressive
    decision_stance: str = Stance.DEFAULT

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "EngineSettings":
        data = _as_dict(raw)
        return cls(
            llm_enabled=_as_bool(data.get("llm_enabled"), cls.llm_enabled),
            llm_timeout_seconds=_as_int(
                data.get("llm_timeout_seconds"), cls.llm_timeout_seconds
            ),
            llm_temperature=_as_float(data.get("llm_temperature"), cls.llm_temperature),
            decision_stance=_normalize_stance_name(
                data.get("decision_stance", cls.decision_stance)
            ),
        )


@dataclass(frozen=True)
class RecordSettings:
    dir: str = "records"

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "RecordSettings":
        return cls(dir=_as_str(_as_dict(raw).get("dir"), cls.dir) or cls.dir)


@dataclass(frozen=True)
class V1Settings:
    path: str = "../PA_Agent"

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "V1Settings":
        return cls(path=_as_str(_as_dict(raw).get("path"), cls.path) or cls.path)


BINANCE_INTERVALS: tuple[str, ...] = (
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
)
BINANCE_MARKETS: tuple[str, ...] = ("spot", "futures")
_DATA_SOURCES: tuple[str, ...] = ("binance", "synthetic", "mt5", "tushare")


@dataclass(frozen=True)
class DataSettings:
    """行情数据源配置（当前计划接币安；真实适配器尚未接线）。"""

    source: str = "binance"
    symbol: str = "BTCUSDT"
    timeframe: str = "15m"
    bar_count: int = 100
    market: str = "spot"                 # spot / futures
    base_url: str = ""                   # 空 = 按 market 选默认
    api_key: str = ""                    # 公共 K 线不需要
    api_secret: str = ""
    request_timeout_seconds: int = 10
    # 旧数据源字段（保留解析，便于以后切回 MT5/Tushare）
    mt5_login: str = ""
    mt5_password: str = ""
    mt5_server: str = ""
    mt5_path: str = ""
    tushare_token: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "DataSettings":
        data = _as_dict(raw)
        return cls(
            source=_as_str(data.get("source"), cls.source),
            symbol=_as_str(data.get("symbol"), cls.symbol),
            timeframe=_as_str(data.get("timeframe"), cls.timeframe),
            bar_count=_as_int(data.get("bar_count"), cls.bar_count),
            market=_as_str(data.get("market"), cls.market),
            base_url=_as_str(data.get("base_url")),
            api_key=_as_str(data.get("api_key")),
            api_secret=_as_str(data.get("api_secret")),
            request_timeout_seconds=_as_int(
                data.get("request_timeout_seconds"), cls.request_timeout_seconds
            ),
            mt5_login=_as_str(data.get("mt5_login")),
            mt5_password=_as_str(data.get("mt5_password")),
            mt5_server=_as_str(data.get("mt5_server")),
            mt5_path=_as_str(data.get("mt5_path")),
            tushare_token=_as_str(data.get("tushare_token")),
        )

    @property
    def resolved_base_url(self) -> str:
        """币安 REST 根地址：显式配置优先，否则按 market 选默认。"""
        if self.base_url:
            return self.base_url.rstrip("/")
        if self.market == "futures":
            return "https://fapi.binance.com"
        return "https://api.binance.com"


@dataclass(frozen=True)
class NotifySettings:
    """通知（缺口：v2 尚未实现）。"""

    pushplus_token: str = ""
    feishu_webhook_url: str = ""
    feishu_secret: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "NotifySettings":
        data = _as_dict(raw)
        return cls(
            pushplus_token=_as_str(data.get("pushplus_token")),
            feishu_webhook_url=_as_str(data.get("feishu_webhook_url")),
            feishu_secret=_as_str(data.get("feishu_secret")),
        )


@dataclass(frozen=True)
class Settings:
    provider: ProviderSettings = field(default_factory=ProviderSettings)
    engine: EngineSettings = field(default_factory=EngineSettings)
    records: RecordSettings = field(default_factory=RecordSettings)
    v1: V1Settings = field(default_factory=V1Settings)
    data: DataSettings = field(default_factory=DataSettings)
    notify: NotifySettings = field(default_factory=NotifySettings)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "Settings":
        data = _as_dict(raw)
        return cls(
            provider=ProviderSettings.from_dict(_as_dict(data.get("provider"))),
            engine=EngineSettings.from_dict(_as_dict(data.get("engine"))),
            records=RecordSettings.from_dict(_as_dict(data.get("records"))),
            v1=V1Settings.from_dict(_as_dict(data.get("v1"))),
            data=DataSettings.from_dict(_as_dict(data.get("data"))),
            notify=NotifySettings.from_dict(_as_dict(data.get("notify"))),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def llm_ready(self) -> bool:
        """显式开启 + provider 三要素齐全，才允许真实网络调用。"""
        return bool(self.engine.llm_enabled and self.provider.ready)


def load_settings(path: Path | str | None = None) -> Settings:
    """读取 JSON 配置。

    - 未显式指定路径且文件缺失：写入默认配置（与 v1 行为一致）；
    - 显式路径缺失/JSON 损坏：返回默认配置并发出 RuntimeWarning；
    - 未知字段忽略。
    """
    target = Path(path) if path is not None else SETTINGS_JSON_PATH
    if not target.exists():
        settings = Settings()
        if path is None:
            try:
                save_settings(settings, target)
            except OSError:
                pass
        return settings
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        warnings.warn(
            f"settings.json 读取失败（{exc}），使用默认配置", RuntimeWarning, stacklevel=2
        )
        return Settings()
    return Settings.from_dict(raw)


def save_settings(settings: Settings, path: Path | str | None = None) -> Path:
    """写回 JSON（明文；调用方负责确保文件在 .gitignore 中）。"""
    target = Path(path) if path is not None else SETTINGS_JSON_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


def validate_settings(settings: Settings) -> list[str]:
    """检查必填/取值范围；返回问题列表（空列表=通过）。

    只做「配置是否可用」的静态检查，不发网络请求、不校验密钥真伪。
    """
    issues: list[str] = []
    data = settings.data

    if data.source not in _DATA_SOURCES:
        issues.append(f"data.source={data.source!r} 非法，可选：{', '.join(_DATA_SOURCES)}")

    if data.source == "binance":
        if not data.symbol:
            issues.append("data.symbol 未配置（币安交易对，如 BTCUSDT）")
        if data.timeframe not in BINANCE_INTERVALS:
            issues.append(
                f"data.timeframe={data.timeframe!r} 非法，可选：{', '.join(BINANCE_INTERVALS)}"
            )
        if not 1 <= data.bar_count <= 1000:
            issues.append("data.bar_count 应在 1–1000（币安单次 K 线上限 1000）")
        if data.market not in BINANCE_MARKETS:
            issues.append(
                f"data.market={data.market!r} 非法，可选：{', '.join(BINANCE_MARKETS)}"
            )

    if settings.engine.llm_enabled:
        if not settings.provider.base_url:
            issues.append("provider.base_url 未配置（engine.llm_enabled=true 时必填）")
        if not settings.provider.api_key:
            issues.append("provider.api_key 未配置（engine.llm_enabled=true 时必填）")

    if settings.engine.decision_stance not in Stance.ORDER:
        issues.append(
            f"engine.decision_stance={settings.engine.decision_stance!r} 非法，"
            f"可选：{', '.join(Stance.ORDER)}"
        )

    return issues


__all__ = [
    "BINANCE_INTERVALS",
    "BINANCE_MARKETS",
    "DataSettings",
    "EngineSettings",
    "NotifySettings",
    "ProviderSettings",
    "RecordSettings",
    "Settings",
    "V1Settings",
    "load_settings",
    "save_settings",
    "validate_settings",
]
