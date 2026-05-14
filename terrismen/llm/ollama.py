from __future__ import annotations

import base64

import httpx

from terrismen.llm.base import BaseProvider, ImageInput, ProviderError, normalize_think_level


class OllamaProvider(BaseProvider):
    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._client = httpx.Client(timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=30.0))

    def _endpoint(self) -> str:
        return f"{self.settings.base_url.rstrip('/')}/api/chat"

    def _think_payload(self) -> bool | str | None:
        think_level = normalize_think_level(self.settings.think_level)
        if self.settings.model.strip().lower().startswith("gpt-oss"):
            if think_level == "off":
                return None
            return think_level
        return think_level != "off"

    def complete(self, system_prompt: str, user_prompt: str, *, images: list[ImageInput] | None = None) -> str:
        image_payload = [base64.b64encode(image.data).decode("ascii") for image in images or []]
        payload: dict[str, object] = {
            "model": self.settings.model,
            "stream": False,
            "options": {"temperature": self.settings.temperature},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt, "images": image_payload},
            ],
        }
        think_payload = self._think_payload()
        if think_payload is not None:
            payload["think"] = think_payload
        response = self._post_json(
            self._endpoint(),
            payload=payload,
            image_count=len(image_payload),
        )
        if response.status_code >= 400:
            raise ProviderError(f"Ollama provider error {response.status_code}: {response.text}")

        payload = response.json()
        try:
            return payload["message"]["content"].strip()
        except (KeyError, AttributeError) as exc:
            raise ProviderError(f"Unexpected Ollama response: {payload}") from exc
