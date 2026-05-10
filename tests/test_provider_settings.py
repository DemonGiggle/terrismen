from __future__ import annotations

from terrismen.llm.base import ProviderSettings
from terrismen.llm.ollama import OllamaProvider
from terrismen.llm.openai_compatible import OpenAICompatibleProvider


def build_settings() -> ProviderSettings:
    return ProviderSettings(
        provider_type="openai_compatible",
        base_url="http://localhost:11434",
        model="test-model",
        api_key="",
        temperature=0.2,
        llm_timeout_seconds=900.0,
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
