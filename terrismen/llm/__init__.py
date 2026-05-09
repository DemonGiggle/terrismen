from __future__ import annotations

from terrismen.llm.base import BaseProvider, ImageInput, ProviderError, ProviderSettings
from terrismen.llm.ollama import OllamaProvider
from terrismen.llm.openai_compatible import OpenAICompatibleProvider


def build_provider(settings: ProviderSettings) -> BaseProvider:
    if settings.provider_type == "openai_compatible":
        return OpenAICompatibleProvider(settings)
    if settings.provider_type == "ollama":
        return OllamaProvider(settings)
    raise ProviderError(f"Unsupported provider type: {settings.provider_type}")
