from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


ProviderType = Literal["openai_compatible", "ollama"]


class ProviderSettingsPayload(BaseModel):
    data_root: str = Field(min_length=1)
    provider_type: ProviderType
    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_key: str = ""
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    llm_timeout_seconds: float = Field(default=600.0, ge=60.0, le=3600.0)

    @field_validator("data_root", "base_url", "model", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("api_key", mode="before")
    @classmethod
    def strip_api_key(cls, value: str) -> str:
        return value.strip()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    document_ids: list[int] = Field(default_factory=list)

    @field_validator("message", mode="before")
    @classmethod
    def strip_message(cls, value: str) -> str:
        return value.strip()

    @field_validator("document_ids")
    @classmethod
    def dedupe_document_ids(cls, value: list[int]) -> list[int]:
        seen: set[int] = set()
        deduped: list[int] = []
        for document_id in value:
            if document_id <= 0 or document_id in seen:
                continue
            seen.add(document_id)
            deduped.append(document_id)
        return deduped
