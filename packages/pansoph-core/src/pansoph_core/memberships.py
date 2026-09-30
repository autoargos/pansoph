"""Reconcile a subject's group memberships in the authz engine (CM-H1).

The query path writes `member` tuples for the caller's current effective groups, so the
engine can resolve group grants. Written alone they only ever accumulate: a user who
left a group stayed a member in SpiceDB, and only the vector pre-filter hid the group's
documents, which made the pre-filter (never the source of truth) the thing enforcing a
revocation. This module remembers the set last written per (tenant, user) and deletes
the memberships that are no longer current before the check runs.

The store is a tiny Postgres table keyed by (tenant, user), read and written once per
query, next to the group alias read the same path already does. No em dashes anywhere.
"""

from __future__ import annotations

from typing import Any, Protocol

from pansoph_authz import AuthzPort, ConsistencyToken, membership_tuples

_DDL = """
CREATE TABLE IF NOT EXISTS pansoph_subject_memberships (
    tenant_id  text NOT NULL,
    subject    text NOT NULL,
    groups     text[] NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, subject)
);
"""


class MembershipStore(Protocol):
    async def get(self, tenant_id: str, subject: str) -> set[str] | None:
        """The groups last written for the subject, or None if never written."""
        ...

    async def put(self, tenant_id: str, subject: str, groups: set[str]) -> None:
        """Record the groups just written for the subject."""
        ...


class InMemoryMembershipStore:
    """Process-local store for tests and single-process demos."""

    def __init__(self) -> None:
        self._by_subject: dict[tuple[str, str], set[str]] = {}

    async def get(self, tenant_id: str, subject: str) -> set[str] | None:
        found = self._by_subject.get((tenant_id, subject))
        return set(found) if found is not None else None

    async def put(self, tenant_id: str, subject: str, groups: set[str]) -> None:
        self._by_subject[(tenant_id, subject)] = set(groups)


class PostgresMembershipStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._ready = False

    async def _conn(self) -> Any:
        import psycopg

        conn = await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)
        if not self._ready:
            await conn.execute(_DDL)
            self._ready = True
        return conn

    async def get(self, tenant_id: str, subject: str) -> set[str] | None:
        conn = await self._conn()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT groups FROM pansoph_subject_memberships "
                    "WHERE tenant_id = %s AND subject = %s",
                    (tenant_id, subject),
                )
                row = await cur.fetchone()
        finally:
            await conn.close()
        return set(row[0] or []) if row else None

    async def put(self, tenant_id: str, subject: str, groups: set[str]) -> None:
        conn = await self._conn()
        try:
            await conn.execute(
                "INSERT INTO pansoph_subject_memberships (tenant_id, subject, groups, updated_at) "
                "VALUES (%s, %s, %s, now()) ON CONFLICT (tenant_id, subject) DO UPDATE SET "
                "groups = EXCLUDED.groups, updated_at = EXCLUDED.updated_at",
                (tenant_id, subject, sorted(groups)),
            )
        finally:
            await conn.close()


async def reconcile_memberships(
    authz: AuthzPort,
    store: MembershipStore,
    *,
    subject: str,
    groups: set[str],
    tenant_id: str,
) -> ConsistencyToken | None:
    """Make the engine's memberships for `subject` equal `groups`.

    Deletes the memberships written last time that are no longer current (revocations
    first), then writes the current set (idempotent TOUCH). Returns the token of that
    write. The caller checks at least that fresh: the write is newer than every earlier
    ACL write (SpiceDB revisions are monotonic), so the check sees both this request's
    memberships (a new grant is not missed) and any removal (a revoked one is not read).
    """
    prior = await store.get(tenant_id, subject)
    stale = sorted((prior or set()) - groups)
    if stale:
        await authz.delete_tuples(membership_tuples(subject, stale, tenant_id))
    written = await authz.write_tuples(membership_tuples(subject, sorted(groups), tenant_id))
    if prior != groups:
        await store.put(tenant_id, subject, groups)
    return written or None
