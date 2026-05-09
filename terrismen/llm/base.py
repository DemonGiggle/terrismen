from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class ProviderError(RuntimeError):
    pass


@dataclass(slots=True)
class ImageInput:
    mime_type: str
    data: bytes


@dataclass(slots=True)
class ProviderSettings:
    provider_type: str
    base_url: str
    model: str
    api_key: str
    temperature: float

    def is_configured(self) -> bool:
        return bool(self.base_url and self.model and self.provider_type)


class BaseProvider(ABC):
    def __init__(self, settings: ProviderSettings) -> None:
        self.settings = settings

    @abstractmethod
    def complete(self, system_prompt: str, user_prompt: str, *, images: list[ImageInput] | None = None) -> str:
        raise NotImplementedError
