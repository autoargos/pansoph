"""LLM adapter: any OpenAI compatible chat completions endpoint. Bring your own model.

No provider is hardcoded. The base URL, model, and key come from configuration, so
the same adapter serves a local Ollama or vLLM server and any cloud endpoint. The
`cache` flag is a hint for prompt caching; providers that support it use it, others
ignore it. Used for both contextual context generation and answering.
"""

from __future__ import annotations

import httpx


class OpenAICompatibleLLM:
    def __init__(self, base_url: str, model: str, api_key: str = "") -> None:
        self.model = model
        self._base = base_url.rstrip("/")
        self._key = api_key

    async def complete(self, prompt: str, *, cache: bool = False) -> str:
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with httpx.AsyncClient(timeout=120.0, headers=headers) as client:
            resp = await client.post(f"{self._base}/chat/completions", json=body)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
