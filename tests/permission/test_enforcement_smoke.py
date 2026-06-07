"""A minimal permission-enforcement test, the seed of the red-team gate.

It proves the one invariant that matters most: the retrieval pipeline returns only
documents the authz port allows, even when the vector store surfaces more. The full
red-team suite (cross-user isolation, revocation freshness, tenant isolation) is
task #21; this keeps the gate honest in the meantime.
"""

from __future__ import annotations

import pytest
from pansoph_authz import AuthzPort, ConsistencyToken, Principal, Tuple
from pansoph_retrieval.pipeline import retrieve
from pansoph_retrieval.ports import SearchHit, VectorFilter


class _FakeEmbedder:
    model = "fake"
    dim = 1

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


class _FakeVectorStore:
    """Surfaces four candidates regardless of filter, to test that authz, not the
    vector pre-filter, is the source of truth."""

    def __init__(self) -> None:
        self.last_limit: int | None = None

    async def upsert(
        self, hits: list[SearchHit], vectors: list[list[float]]
    ) -> None:  # pragma: no cover
        return None

    async def hybrid_search(
        self, query_vector: list[float], query_text: str, flt: VectorFilter, limit: int
    ) -> list[SearchHit]:
        self.last_limit = limit
        return [
            SearchHit(doc_id=d, chunk_id=f"{d}#0", score=1.0, tenant_id="t1", allow_groups=["g1"])
            for d in ("a", "b", "c", "d")
        ]


class _FakeAuthz(AuthzPort):
    """Allows only documents a and c."""

    async def write_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:  # pragma: no cover
        return ConsistencyToken("t")

    async def delete_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:  # pragma: no cover
        return ConsistencyToken("t")

    async def bulk_check(
        self,
        subject: Principal,
        permission: str,
        object_ids: list[str],
        at_least_as_fresh: ConsistencyToken | None = None,
    ) -> set[str]:
        return {"a", "c"} & set(object_ids)


@pytest.mark.permission
async def test_retrieval_returns_only_authz_allowed_documents() -> None:
    store = _FakeVectorStore()
    hits = await retrieve(
        query="anything",
        subject=Principal(type="user", id="alice"),
        tenant_id="t1",
        subject_groups=["g1"],
        final_k=4,
        vector_store=store,
        embedder=_FakeEmbedder(),
        authz=_FakeAuthz(),
    )
    returned = {h.doc_id for h in hits}
    assert returned == {"a", "c"}, "denied documents must never survive the pipeline"
    # b and d were surfaced by the vector store but filtered by authz.
    assert "b" not in returned and "d" not in returned


@pytest.mark.permission
async def test_pipeline_overfetches_before_authz_check() -> None:
    store = _FakeVectorStore()
    await retrieve(
        query="q",
        subject=Principal(type="user", id="alice"),
        tenant_id="t1",
        subject_groups=["g1"],
        final_k=4,
        vector_store=store,
        embedder=_FakeEmbedder(),
        authz=_FakeAuthz(),
    )
    # The store must be asked for more than final_k so authz has a margin to filter.
    assert store.last_limit is not None and store.last_limit > 4
