"""感知层客户端：离线 Mock + 可选 HTTP 骨架（默认不联网）。

最小接口：`complete(prompt) -> dict`。
- `MockLLM`：确定性返回，用于离线测试与回放；记录每次调用的 prompt 体积；
- `HttpGrayZoneClient`：OpenAI 兼容 /chat/completions，`enabled=False` 时拒绝调用，
  保证默认运行路径不会产生网络 IO。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from pa_engine.ai.contract import GrayZoneAnswer, parse_response
from pa_engine.config.settings import Settings, load_settings


class MockLLM:
    """离线感知层替身。

    `answer` 中的字段会覆盖默认值；默认返回「无额外信息、质量合格」，
    这样灰区调用不会改变代码决策，便于测试灰区比例与调用次数。
    """

    DEFAULT_ANSWER: dict[str, Any] = {
        "cycle_position": "unknown",
        "direction": "neutral",
        "always_in": "none",
        "signal_bar_quality": "pass",
        "qualitative_notes": "mock: 无额外感知输入",
    }

    def __init__(self, answer: dict[str, Any] | None = None) -> None:
        self.answer = dict(self.DEFAULT_ANSWER)
        if answer:
            self.answer.update(answer)
        self.calls: list[dict[str, float]] = []

    def complete(self, prompt: str) -> dict[str, Any]:
        self.calls.append(
            {"bytes": float(len(prompt.encode("utf-8"))), "chars": float(len(prompt))}
        )
        return dict(self.answer)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def __repr__(self) -> str:
        return f"MockLLM(calls={len(self.calls)})"


class HttpGrayZoneClient:
    """OpenAI 兼容客户端骨架（默认禁用）。"""

    def __init__(
        self,
        *,
        model: str = "",
        base_url: str = "",
        api_key: str = "",
        timeout_seconds: int = 60,
        temperature: float = 0.0,
        enabled: bool = False,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.enabled = enabled

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "HttpGrayZoneClient":
        """按 `config/settings.json` 构造；`engine.llm_enabled=false` 时保持离线。"""
        config = settings or load_settings()
        client = cls(
            model=config.provider.model,
            base_url=config.provider.base_url,
            api_key=config.provider.api_key,
            timeout_seconds=config.engine.llm_timeout_seconds,
            temperature=config.engine.llm_temperature,
            enabled=config.llm_ready,
        )
        return client

    def complete(self, prompt: str) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError(
                "HTTP 感知层未启用；请在 config/settings.json 设置 "
                "engine.llm_enabled=true，并填写 provider.base_url / provider.api_key"
            )
        if not self.base_url:
            raise RuntimeError("缺少 provider.base_url")
        if not self.api_key:
            raise RuntimeError("缺少 provider.api_key")
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"感知层调用失败：{exc}") from exc
        content = (
            data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if isinstance(data, dict)
            else ""
        )
        return {"content": content}

    def ask(self, prompt: str) -> GrayZoneAnswer | None:
        raw = self.complete(prompt)
        text = raw.get("content", "") if isinstance(raw, dict) else str(raw)
        return parse_response(text)

    def __repr__(self) -> str:  # 避免日志泄漏 key
        return f"HttpGrayZoneClient(model={self.model!r}, enabled={self.enabled})"


def dumps(answer: GrayZoneAnswer) -> str:
    """调试辅助：契约对象 → JSON 文本。"""
    return json.dumps(answer.__dict__, ensure_ascii=False)
