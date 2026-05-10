from __future__ import annotations

import base64

import httpx

from terrismen.llm.base import BaseProvider, ImageInput, ProviderError


class OpenAICompatibleProvider(BaseProvider):
    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._client = httpx.Client(timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=30.0))

    def _endpoint(self) -> str:
        base = self.settings.base_url.rstrip("/")
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def complete(self, system_prompt: str, user_prompt: str, *, images: list[ImageInput] | None = None) -> str:
        user_content: str | list[dict[str, object]] = user_prompt
        if images:
            blocks: list[dict[str, object]] = [{"type": "text", "text": user_prompt}]
            for image in images:
                encoded = base64.b64encode(image.data).decode("ascii")
                blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{image.mime_type};base64,{encoded}"},
                    }
                )
            user_content = blocks

        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"

        response = self._client.post(
            self._endpoint(),
            headers=headers,
            json={
                "model": self.settings.model,
                "temperature": self.settings.temperature,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            },
        )
        if response.status_code >= 400:
            raise ProviderError(f"OpenAI-compatible provider error {response.status_code}: {response.text}")

        payload = response.json()
        try:
            return payload["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, AttributeError) as exc:
            raise ProviderError(f"Unexpected OpenAI-compatible response: {payload}") from exc
