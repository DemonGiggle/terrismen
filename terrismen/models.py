from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


ProviderType = Literal["openai_compatible", "ollama"]


class ProviderSettingsPayload(BaseModel):
    provider_type: ProviderType
    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key: str = ""
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)

    @field_validator("base_url", "model", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("api_key", mode="before")
    @classmethod
    def strip_api_key(cls, value: str) -> str:
        return value.strip()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)

    @field_validator("message", mode="before")
    @classmethod
    def strip_message(cls, value: str) -> str:
        return value.strip()
