"""Shared in-memory fakes for the I/O ports, used across the test suite.

They implement the ports faithfully enough to exercise the real pipeline, mappers,
and enforcement logic without Postgres, SpiceDB, or a model server. The FakeAuthz in
particular mirrors how a real engine resolves the wildcard, direct-user, and
nested-group reader tuples that the mappers write.
"""

from __future__ import annotations

from pansoph_authz import TENANT_EVERYONE, AuthzPort, ConsistencyToken, Principal, Tuple
from pansoph_retrieval.ports import SearchHit, VectorFilter


class FakeEmbedder:
    model = "fake"
    dim = 3

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0, 0.0] for t in texts]


class FakeVectorStore:
    """Applies the coarse pre-filter: tenant match and (no groups or group overlap)."""

    def __init__(self) -> None:
        self.rows: list[SearchHit] = []
        self.last_limit: int | None = None

    async def upsert(self, hits: list[SearchHit], vectors: list[list[float]]) -> None:
        self.rows.extend(hits)

    async def hybrid_search(
        self, query_vector: list[float], query_text: str, flt: VectorFilter, limit: int
    ) -> list[SearchHit]:
        self.last_limit = limit
        out = [
            h
            for h in self.rows
            if h.tenant_id == flt.tenant_id
            and (not h.allow_groups or set(h.allow_groups) & set(flt.any_groups))
        ]
        return out[:limit]


class FakeAuthz(AuthzPort):
    """Resolves reader tuples like a real engine: wildcard, direct user, or a group
    the subject belongs to (membership supplied at construction)."""

    def __init__(self, user_groups: dict[str, set[str]] | None = None) -> None:
        self._user_groups = user_groups or {}
        self.tuples: set[tuple[str, str, str]] = set()

    async def write_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:
        for t in tuples:
            self.tuples.add((t.subject.type, t.subject.id, t.object_id))
        return ConsistencyToken("z")

    async def delete_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:
        for t in tuples:
            self.tuples.discard((t.subject.type, t.subject.id, t.object_id))
        return ConsistencyToken("z")

    async def bulk_check(
        self,
        subject: Principal,
        permission: str,
        object_ids: list[str],
        at_least_as_fresh: ConsistencyToken | None = None,
    ) -> set[str]:
        # Every user is a member of its tenant everyone group, so public docs (granted
        # to that group) resolve. The fake is tenant-blind, matching the real engine's
        # group resolution for the suite's single-tenant scenarios.
        groups = self._user_groups.get(subject.id, set()) | {TENANT_EVERYONE}
        allowed: set[str] = set()
        for oid in object_ids:
            if ("user", "*", oid) in self.tuples or ("user", subject.id, oid) in self.tuples:
                allowed.add(oid)
            elif any(("group", g, oid) in self.tuples for g in groups):
                allowed.add(oid)
        return allowed
