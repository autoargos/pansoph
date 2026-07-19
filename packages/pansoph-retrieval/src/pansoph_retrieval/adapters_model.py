"""Model serving adapters: embeddings and reranking over HTTP. Bring your own model.

TEIEmbedder and TEIReranker target the HF Text Embeddings Inference native API
(`/embed` and `/rerank`). OpenAICompatibleEmbedder targets any OpenAI compatible
`/embeddings` endpoint. None of them hardcode a provider; the URL and model come
from configuration.
"""

from __future__ import annotations

import httpx

from .ports import SearchHit

# A document's chunks are embedded together, but a large file (a long PDF or a big
# spreadsheet) can produce dozens or hundreds of chunks, and sending them all in one
# request makes a local model (Ollama, vLLM on CPU) run past the HTTP read timeout, which
# aborts the whole sync (the reported Google Drive "httpx.ReadTimeout"). Requests are
# batched so each one is bounded regardless of how many chunks a document has.
_EMBED_BATCH = 16
_EMBED_TIMEOUT = 120.0
# A neutral input embedded when the server rejects a specific chunk (a 400 the batch and
# single-item retry both hit), so that one unembeddable chunk still gets a vector and the
# document indexes instead of the whole sync aborting.
_EMBED_FALLBACK_INPUT = " "


def _batched(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class TEIEmbedder:
    def __init__(self, url: str, model: str = "BAAI/bge-m3", dim: int = 1024) -> None:
        self.model = model
        self.dim = dim
        self._url = url.rstrip("/")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=_EMBED_TIMEOUT) as client:
            for batch in _batched(texts, _EMBED_BATCH):
                resp = await client.post(f"{self._url}/embed", json={"inputs": batch})
                resp.raise_for_status()
                out.extend(resp.json())
        return out


class OpenAICompatibleEmbedder:
    def __init__(self, base_url: str, model: str, api_key: str = "", dim: int = 1024) -> None:
        self.model = model
        self.dim = dim
        self._base = base_url.rstrip("/")
        self._key = api_key

    async def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=_EMBED_TIMEOUT, headers=headers) as client:
            for batch in _batched(texts, _EMBED_BATCH):
                try:
                    out.extend(await self._post(client, batch))
                except httpx.HTTPStatusError:
                    # The server rejected the batch (a too-large batch, or one odd chunk
                    # some local servers 400 on). Retry one item at a time so a single
                    # unembeddable chunk cannot abort the whole sync; a chunk that is still
                    # rejected gets a neutral embedding so its document is still indexed.
                    out.extend(await self._post_singly(client, batch))
        return out

    async def _post(self, client: httpx.AsyncClient, batch: list[str]) -> list[list[float]]:
        # Request the configured dimension. Matryoshka-capable models (gemini-embedding,
        # OpenAI text-embedding-3) honor it; others ignore it.
        body = {"model": self.model, "input": batch, "dimensions": self.dim}
        resp = await client.post(f"{self._base}/embeddings", json=body)
        resp.raise_for_status()
        data = resp.json()["data"]
        # OpenAI includes an "index" per item; some compatible servers (Gemini) do not,
        # so fall back to input order within the batch.
        return [item["embedding"] for item in sorted(data, key=lambda x: x.get("index", 0))]

    async def _post_singly(self, client: httpx.AsyncClient, batch: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in batch:
            try:
                out.extend(await self._post(client, [text]))
            except httpx.HTTPStatusError:
                out.extend(await self._post(client, [_EMBED_FALLBACK_INPUT]))
        return out


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
