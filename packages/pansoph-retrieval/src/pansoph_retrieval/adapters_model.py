"""Model serving adapters: embeddings and reranking over HTTP. Bring your own model.

TEIEmbedder and TEIReranker target the HF Text Embeddings Inference native API
(`/embed` and `/rerank`). OpenAICompatibleEmbedder targets any OpenAI compatible
`/embeddings` endpoint. None of them hardcode a provider; the URL and model come
from configuration.
"""

from __future__ import annotations

import httpx

from .ports import SearchHit


class TEIEmbedder:
    def __init__(self, url: str, model: str = "BAAI/bge-m3", dim: int = 1024) -> None:
        self.model = model
        self.dim = dim
        self._url = url.rstrip("/")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{self._url}/embed", json={"inputs": texts})
            resp.raise_for_status()
            return resp.json()


class OpenAICompatibleEmbedder:
    def __init__(self, base_url: str, model: str, api_key: str = "", dim: int = 1024) -> None:
        self.model = model
        self.dim = dim
        self._base = base_url.rstrip("/")
        self._key = api_key

    async def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
            resp = await client.post(
                f"{self._base}/embeddings", json={"model": self.model, "input": texts}
            )
            resp.raise_for_status()
            data = resp.json()["data"]
            return [item["embedding"] for item in sorted(data, key=lambda x: x["index"])]


class TEIReranker:
    def __init__(self, url: str, model: str = "BAAI/bge-reranker-v2-m3") -> None:
        self.model = model
        self._url = url.rstrip("/")

    async def rerank(self, query: str, hits: list[SearchHit], top_n: int) -> list[SearchHit]:
        if not hits:
            return []
        texts = [hit.text or "" for hit in hits]
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{self._url}/rerank", json={"query": query, "texts": texts})
            resp.raise_for_status()
            ranked = resp.json()  # [{"index": i, "score": s}, ...]
        order = sorted(ranked, key=lambda x: x["score"], reverse=True)[:top_n]
        return [
            hits[item["index"]].model_copy(update={"score": float(item["score"])}) for item in order
        ]
