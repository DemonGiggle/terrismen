from __future__ import annotations

import json

from terrismen.llm.base import ProviderSettings
from terrismen.llm.ollama import OllamaProvider
from terrismen.llm.openai_compatible import OpenAICompatibleProvider


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.status_code = 200
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict[str, object]:
        return self._payload


def build_settings() -> ProviderSettings:
    return ProviderSettings(
        provider_type="openai_compatible",
        base_url="http://localhost:11434",
        model="test-model",
        api_key="",
        temperature=0.2,
        llm_timeout_seconds=900.0,
        think_level="off",
    )


def test_openai_provider_uses_configured_timeout() -> None:
    provider = OpenAICompatibleProvider(build_settings())

    assert provider._client.timeout.read == 900.0
    assert provider._client.timeout.connect == 30.0


def test_ollama_provider_uses_configured_timeout() -> None:
    settings = build_settings()
    settings.provider_type = "ollama"
    provider = OllamaProvider(settings)

    assert provider._client.timeout.read == 900.0
    assert provider._client.timeout.connect == 30.0


def test_openai_provider_does_not_send_think_field(monkeypatch) -> None:
    provider = OpenAICompatibleProvider(build_settings())
    captured: dict[str, object] = {}

    def fake_post(endpoint, headers=None, json=None):
        captured["json"] = json
        return FakeResponse({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(provider._client, "post", fake_post)

    provider.complete("system prompt", "user prompt")

    assert "think" not in captured["json"]


def test_ollama_non_gpt_oss_models_use_boolean_think(monkeypatch) -> None:
    settings = build_settings()
    settings.provider_type = "ollama"
    settings.model = "deepseek-r1"
    settings.think_level = "medium"
    provider = OllamaProvider(settings)
    captured: dict[str, object] = {}

    def fake_post(endpoint, headers=None, json=None):
        captured["json"] = json
        return FakeResponse({"message": {"content": "ok"}})

    monkeypatch.setattr(provider._client, "post", fake_post)

    provider.complete("system prompt", "user prompt")

    assert captured["json"]["think"] is True


def test_ollama_non_gpt_oss_models_send_false_when_think_is_off(monkeypatch) -> None:
    settings = build_settings()
    settings.provider_type = "ollama"
    settings.model = "deepseek-r1"
    settings.think_level = "off"
    provider = OllamaProvider(settings)
    captured: dict[str, object] = {}

    def fake_post(endpoint, headers=None, json=None):
        captured["json"] = json
        return FakeResponse({"message": {"content": "ok"}})

    monkeypatch.setattr(provider._client, "post", fake_post)

    provider.complete("system prompt", "user prompt")

    assert captured["json"]["think"] is False


def test_ollama_gpt_oss_models_use_level_think(monkeypatch) -> None:
    settings = build_settings()
    settings.provider_type = "ollama"
    settings.model = "gpt-oss:20b"
    settings.think_level = "high"
    provider = OllamaProvider(settings)
    captured: dict[str, object] = {}

    def fake_post(endpoint, headers=None, json=None):
        captured["json"] = json
        return FakeResponse({"message": {"content": "ok"}})

    monkeypatch.setattr(provider._client, "post", fake_post)

    provider.complete("system prompt", "user prompt")

    assert captured["json"]["think"] == "high"


def test_ollama_gpt_oss_off_omits_think_field(monkeypatch) -> None:
    settings = build_settings()
    settings.provider_type = "ollama"
    settings.model = "gpt-oss"
    settings.think_level = "off"
    provider = OllamaProvider(settings)
    captured: dict[str, object] = {}

    def fake_post(endpoint, headers=None, json=None):
        captured["json"] = json
        return FakeResponse({"message": {"content": "ok"}})

    monkeypatch.setattr(provider._client, "post", fake_post)

    provider.complete("system prompt", "user prompt")

    assert "think" not in captured["json"]
