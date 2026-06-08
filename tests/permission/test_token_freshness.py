"""Consistency-token threading: retrieval reads no staler than the freshest ACL
write among the candidates, so a revocation is never served stale.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _fakes import FakeEmbedder  # noqa: E402
from pansoph_authz import AuthzPort, ConsistencyToken, Principal, Tuple  # noqa: E402
from pansoph_retrieval.pipeline import retrieve  # noqa: E402
from pansoph_retrieval.ports import SearchHit, VectorFilter  # noqa: E402


class _RecordingAuthz(AuthzPort):
    def __init__(self) -> None:
        self.seen: ConsistencyToken | None = None

    async def write_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:  # pragma: no cover
        return ConsistencyToken("z")

    async def delete_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:  # pragma: no cover
        return ConsistencyToken("z")

    async def bulk_check(
        self,
        subject: Principal,
        permission: str,
        object_ids: list[str],
        at_least_as_fresh: ConsistencyToken | None = None,
    ) -> set[str]:
        self.seen = at_least_as_fresh
        return set(object_ids)


class _TokenStore:
    async def upsert(self, hits: list[SearchHit], vectors: list[list[float]]) -> None:
        return None

    async def hybrid_search(
        self, query_vector: list[float], query_text: str, flt: VectorFilter, limit: int
    ) -> list[SearchHit]:
        return [
            SearchHit(
                doc_id="a",
                chunk_id="a#0",
                score=1.0,
                tenant_id="t1",
                consistency_token="tok-old",
                acl_written_at=1.0,
            ),
            SearchHit(
                doc_id="b",
                chunk_id="b#0",
                score=1.0,
                tenant_id="t1",
                consistency_token="tok-new",
                acl_written_at=2.0,
            ),
        ]


@pytest.mark.permission
async def test_retrieve_threads_freshest_consistency_token() -> None:
    authz = _RecordingAuthz()
    await retrieve(
        query="q",
        subject=Principal(type="user", id="alice"),
        tenant_id="t1",
        subject_groups=[],
        final_k=4,
        vector_store=_TokenStore(),
        embedder=FakeEmbedder(),
        authz=authz,
    )
    # The freshest candidate (max acl_written_at) wins, so the bulk check reads at
    # least as fresh as the most recent ACL write among the candidates.
    assert str(authz.seen) == "tok-new"
