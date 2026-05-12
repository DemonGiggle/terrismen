from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from terrismen.debug import current_llm_operation_context, find_llm_caller, log_debug_event, next_llm_request_id


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
    llm_timeout_seconds: float

    def is_configured(self) -> bool:
        return bool(self.base_url and self.model and self.provider_type)


class BaseProvider(ABC):
    def __init__(self, settings: ProviderSettings) -> None:
        self.settings = settings

    def _post_json(
        self,
        endpoint: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, object],
        image_count: int = 0,
    ) -> httpx.Response:
        request_id = next_llm_request_id()
        caller = find_llm_caller()
        context = current_llm_operation_context()
        request_metadata = {
            "request_id": request_id,
            "provider_type": self.settings.provider_type,
            "model": self.settings.model,
            "endpoint": endpoint,
            "timeout_seconds": self.settings.llm_timeout_seconds,
            "prompt_chars_system": len(str(payload.get("messages", [{}])[0].get("content", ""))) if payload.get("messages") else 0,
            "prompt_chars_user": len(str(payload.get("messages", [{}, {}])[1].get("content", ""))) if payload.get("messages") else 0,
            "image_count": image_count,
            **caller,
            **context,
        }
        log_debug_event("llm_request_start", **request_metadata)
        started_at = time.perf_counter()
        try:
            response = self._client.post(endpoint, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
            message = (
                f"LLM request timed out after {self.settings.llm_timeout_seconds:.1f}s "
                f"calling {endpoint} ({type(exc).__name__})"
            )
            log_debug_event(
                "llm_request_timeout",
                **request_metadata,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
                error_message=message,
            )
            raise ProviderError(message) from exc
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
            log_debug_event(
                "llm_request_error",
                **request_metadata,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
                error_message=str(exc) or type(exc).__name__,
            )
            raise
        duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
        log_debug_event(
            "llm_request_end",
            **request_metadata,
            duration_ms=duration_ms,
            status_code=response.status_code,
        )
        return response

    @abstractmethod
    def complete(self, system_prompt: str, user_prompt: str, *, images: list[ImageInput] | None = None) -> str:
        raise NotImplementedError
