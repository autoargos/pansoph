"""End-to-end vertical: ingest, then ACL-aware retrieve, with fakes for the I/O ports.

This runs the real ingestion pipeline, the real ACL-to-tuple mappers, and the real
retrieval enforcement path. Only the embedder, vector store, and authz engine are
fakes, so the wiring and the permission logic are exercised without Postgres or
SpiceDB. It is the headline red-team in miniature: a user with a group sees the
group document, a user without it does not.

The exhaustive suite is task #21; this proves the vertical works today.
"""

from __future__ import annotations

import pytest
from pansoph_authz import TENANT_EVERYONE, AuthzPort, ConsistencyToken, Principal, Tuple
from pansoph_connector_sdk import InMemoryConnector
from pansoph_ingestion.adapters_parser import DoclingParser
from pansoph_ingestion.pipeline import IngestionPipeline
from pansoph_retrieval.pipeline import retrieve
from pansoph_retrieval.ports import SearchHit, VectorFilter
from pansoph_types import AccessControl


class _FakeEmbedder:
    model = "fake"
    dim = 3

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0, 0.0] for t in texts]


class _FakeVectorStore:
    """Mimics the coarse pre-filter: tenant match and group overlap, or no groups."""

    def __init__(self) -> None:
        self.rows: list[tuple[SearchHit, list[float]]] = []

    async def upsert(self, hits: list[SearchHit], vectors: list[list[float]]) -> None:
        self.rows.extend(zip(hits, vectors, strict=True))

    async def hybrid_search(
        self, query_vector: list[float], query_text: str, flt: VectorFilter, limit: int
    ) -> list[SearchHit]:
        out = [
            hit
            for hit, _ in self.rows
            if hit.tenant_id == flt.tenant_id
            and (not hit.allow_groups or set(hit.allow_groups) & set(flt.any_groups))
        ]
        return out[:limit]


class _FakeAuthz(AuthzPort):
    """Resolves group membership from a known map, like SpiceDB's nested groups."""

    def __init__(self, user_groups: dict[str, set[str]]) -> None:
        self._user_groups = user_groups
        self._tuples: set[tuple[str, str, str]] = set()

    async def write_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:
        for t in tuples:
            self._tuples.add((t.subject.type, t.subject.id, t.object_id))
        return ConsistencyToken("z1")

    async def delete_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:
        for t in tuples:
            self._tuples.discard((t.subject.type, t.subject.id, t.object_id))
        return ConsistencyToken("z2")

    async def bulk_check(
        self,
        subject: Principal,
        permission: str,
        object_ids: list[str],
        at_least_as_fresh: ConsistencyToken | None = None,
    ) -> set[str]:
        # Every user is a member of its tenant everyone group, so public docs resolve.
        groups = self._user_groups.get(subject.id, set()) | {TENANT_EVERYONE}
        allowed: set[str] = set()
        for oid in object_ids:
            if ("user", "*", oid) in self._tuples or ("user", subject.id, oid) in self._tuples:
                allowed.add(oid)
            elif any(("group", g, oid) in self._tuples for g in groups):
                allowed.add(oid)
        return allowed


async def _ingest() -> tuple[_FakeVectorStore, _FakeAuthz]:
    connector = InMemoryConnector()
    connector.put("secret", "engineering only material", AccessControl(allow_groups=["g1"]))
    connector.put("handbook", "everyone can read this", AccessControl(public=True))

    store = _FakeVectorStore()
    authz = _FakeAuthz(user_groups={"alice": {"g1"}, "bob": set()})
    pipeline = IngestionPipeline(
        connector=connector,
        parser=DoclingParser(),
        embedder=_FakeEmbedder(),
        vector_store=store,
        authz=authz,
    )
    await pipeline.sync(connector_name="memory", tenant_id="t1")
    return store, authz


async def _retrieve_for(store: _FakeVectorStore, authz: _FakeAuthz, user: str, groups: list[str]):
    return await retrieve(
        query="material",
        subject=Principal(type="user", id=user),
        tenant_id="t1",
        subject_groups=groups,
        final_k=10,
        vector_store=store,
        embedder=_FakeEmbedder(),
        authz=authz,
    )


@pytest.mark.permission
async def test_group_member_sees_restricted_doc() -> None:
    store, authz = await _ingest()
    hits = await _retrieve_for(store, authz, "alice", ["g1"])
    docs = {h.doc_id for h in hits}
    assert "memory:mem:secret" in docs
    assert "memory:mem:handbook" in docs


@pytest.mark.permission
async def test_non_member_never_sees_restricted_doc() -> None:
    store, authz = await _ingest()
    hits = await _retrieve_for(store, authz, "bob", [])
    docs = {h.doc_id for h in hits}
    assert "memory:mem:handbook" in docs, "bob should still see the public handbook"
    assert "memory:mem:secret" not in docs, "bob must never retrieve the group-only document"
