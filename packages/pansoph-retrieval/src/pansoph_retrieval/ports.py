"""Retrieval ports: VectorStore, Embedder, Reranker. All model and engine agnostic."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class SearchHit(BaseModel):
    """One retrieved chunk with the metadata the enforcement path needs."""

    doc_id: str
    chunk_id: str
    score: float
    tenant_id: str
    allow_groups: list[str] = Field(default_factory=list)
    # Explicit denies, carried from the source ACL. Applied as a final filter in the
    # retrieval path, never as an authz exclusion tuple (exclusion operators wreck
    # bulk-check latency). A deny entry is a user id or a group name.
    deny: list[str] = Field(default_factory=list)
    acl_version: int = 0
    text: str | None = None


class VectorFilter(BaseModel):
    """The coarse pre filter. tenant_id is mandatory and server enforced."""

    tenant_id: str
    any_groups: list[str] = Field(default_factory=list)


@runtime_checkable
class Embedder(Protocol):
    model: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Provider agnostic, BYOM."""
        ...


@runtime_checkable
class Reranker(Protocol):
    model: str

    async def rerank(self, query: str, hits: list[SearchHit], top_n: int) -> list[SearchHit]:
        """Reorder hits by cross encoder relevance and keep the top_n."""
        ...


@runtime_checkable
class VectorStore(Protocol):
    async def upsert(self, hits: list[SearchHit], vectors: list[list[float]]) -> None:
        """Upsert chunks with their dense vectors and ACL metadata payload."""
        ...

    async def hybrid_search(
        self,
        query_vector: list[float],
        query_text: str,
        flt: VectorFilter,
        limit: int,
    ) -> list[SearchHit]:
        """Dense plus sparse search with the coarse pre filter, fused with RRF.

        `limit` is already the overfetched count (3 to 5 times the final need).
        """
        ...
