from __future__ import annotations

import base64

import httpx

from terrismen.llm.base import BaseProvider, ImageInput, ProviderError


class OllamaProvider(BaseProvider):
    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._client = httpx.Client(timeout=180.0)

    def _endpoint(self) -> str:
        return f"{self.settings.base_url.rstrip('/')}/api/chat"

    def complete(self, system_prompt: str, user_prompt: str, *, images: list[ImageInput] | None = None) -> str:
        image_payload = [base64.b64encode(image.data).decode("ascii") for image in images or []]
        response = self._client.post(
            self._endpoint(),
            json={
                "model": self.settings.model,
                "stream": False,
                "options": {"temperature": self.settings.temperature},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt, "images": image_payload},
                ],
            },
        )
        if response.status_code >= 400:
            raise ProviderError(f"Ollama provider error {response.status_code}: {response.text}")

        payload = response.json()
        try:
            return payload["message"]["content"].strip()
        except (KeyError, AttributeError) as exc:
            raise ProviderError(f"Unexpected Ollama response: {payload}") from exc
