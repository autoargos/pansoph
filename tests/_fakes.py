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
        # When True, skip the group pre-filter (tenant only), so a test proves the authz
        # bulk check alone keeps unauthorized documents out.
        self.ignore_group_prefilter = False

    async def upsert(self, hits: list[SearchHit], vectors: list[list[float]]) -> None:
        # Replace-by-chunk so a re-upsert of a document does not duplicate its chunks,
        # mirroring the real ON CONFLICT (chunk_id) DO UPDATE.
        incoming = {h.chunk_id for h in hits}
        self.rows = [r for r in self.rows if r.chunk_id not in incoming]
        self.rows.extend(hits)

    async def delete_by_doc_id(self, doc_id: str) -> None:
        # Purge every chunk of a document. Idempotent, like the real DELETE.
        self.rows = [r for r in self.rows if r.doc_id != doc_id]

    async def get_document_chunks(self, doc_id: str, tenant_id: str) -> list[SearchHit]:
        rows = [r for r in self.rows if r.doc_id == doc_id and r.tenant_id == tenant_id]
        return sorted(rows, key=lambda r: int(r.chunk_id.rsplit("#", 1)[-1]))

    async def hybrid_search(
        self, query_vector: list[float], query_text: str, flt: VectorFilter, limit: int
    ) -> list[SearchHit]:
        self.last_limit = limit
        out = [
            h
            for h in self.rows
            if h.tenant_id == flt.tenant_id
            and (
                self.ignore_group_prefilter
                or not h.allow_groups
                or set(h.allow_groups) & set(flt.any_groups)
            )
        ]
        return out[:limit]


def _rid(principal: Principal) -> str:
    """Render a principal id exactly like the real SpiceDB/OpenFGA adapters: namespaced
    as `{ns}_{id}`, wildcard passthrough. So a namespace mismatch between a written tuple
    and a query subject is caught by the fake, not silently accepted."""
    if principal.id == "*":
        return "*"
    return f"{principal.ns}_{principal.id}" if principal.ns else principal.id


def _oid(t: Tuple) -> str:
    """Render a namespaced object id (a group's) exactly like `_rid` renders a namespaced
    subject, so a group referenced as a membership object matches the same group granted as
    a reader tuple subject."""
    return f"{t.object_ns}_{t.object_id}" if t.object_ns else t.object_id


class FakeAuthz(AuthzPort):
    """Resolves reader tuples like a real engine, honoring the principal namespace:
    wildcard, a direct user reader, or a group the subject is a member of. Membership is
    resolved from written member tuples (like the real engine), with an optional legacy
    seed for tests that set it directly. Ids are namespaced exactly like the adapters."""

    def __init__(self, user_groups: dict[str, set[str]] | None = None) -> None:
        self._seed_groups = user_groups or {}
        self.readers: set[tuple[str, str, str]] = set()  # (subject_type, rendered_id, object_id)
        self.members: set[tuple[str, str]] = set()  # (rendered_user_id, rendered_group_object_id)

    async def write_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:
        for t in tuples:
            if t.relation == "member":
                self.members.add((_rid(t.subject), _oid(t)))
            else:
                self.readers.add((t.subject.type, _rid(t.subject), t.object_id))
        return ConsistencyToken("z")

    async def delete_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:
        for t in tuples:
            if t.relation == "member":
                self.members.discard((_rid(t.subject), _oid(t)))
            else:
                self.readers.discard((t.subject.type, _rid(t.subject), t.object_id))
        return ConsistencyToken("z")

    async def bulk_check(
        self,
        subject: Principal,
        permission: str,
        object_ids: list[str],
        at_least_as_fresh: ConsistencyToken | None = None,
    ) -> set[str]:
        sid = _rid(subject)
        # Groups the subject is a member of, from written member tuples plus any legacy
        # seed membership, plus the tenant everyone group. Seed and everyone are rendered
        # in the subject's namespace so they match the tenant-scoped group reader tuples.
        ns = f"{subject.ns}_" if subject.ns else ""
        member_groups = {g for (u, g) in self.members if u == sid}
        member_groups.add(f"{ns}{TENANT_EVERYONE}")
        for g in self._seed_groups.get(subject.id, set()):
            member_groups.add(f"{ns}{g}")
        allowed: set[str] = set()
        for oid in object_ids:
            if ("user", "*", oid) in self.readers or ("user", sid, oid) in self.readers:
                allowed.add(oid)
            elif any(("group", g, oid) in self.readers for g in member_groups):
                allowed.add(oid)
        return allowed
