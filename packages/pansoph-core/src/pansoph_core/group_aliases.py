"""Identity group aliases: the admin declared bridge from an IdP group to a connector
source group.

A connector fails closed by granting a namespaced source group (slack:member:{instance},
jira:licensed, confluence space-role:{id}, ...) that no pansoph identity is a member of
until an operator connects it to their own identity groups. This module stores those
operator declared edges and expands a user's identity groups into the connector groups
they should also be treated as a member of.

The expansion is applied at query time to the user's effective group set, which drives
BOTH the authz membership sync (so the bulk check passes) AND the vector pre filter (so a
document granted only to the connector group is not dropped before the check). The mapping
never widens beyond what the operator declared: an identity group with no alias expands to
nothing, so a document stays unreachable until the operator opts a group in.

The store is Postgres backed and connector agnostic (it stores opaque group id strings),
consistent with the ports philosophy: pansoph does not need to know a connector's group
naming, the operator declares the exact edge.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

_DDL = """
CREATE TABLE IF NOT EXISTS pansoph_group_aliases (
    tenant_id       text NOT NULL,
    identity_group  text NOT NULL,
    connector_group text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, identity_group, connector_group)
);
CREATE INDEX IF NOT EXISTS pansoph_group_aliases_tenant ON pansoph_group_aliases (tenant_id);
"""


def expand_groups(identity_groups: Iterable[str], aliases: Mapping[str, set[str]]) -> list[str]:
    """Expand a user's identity groups with the connector groups an operator aliased them
    to. Returns the union (identity groups plus every mapped connector group), deduplicated
    and sorted for determinism. An identity group with no alias contributes only itself, so
    the expansion can never grant a connector group the operator did not declare.
    """
    effective: set[str] = set()
    for group in identity_groups:
        effective.add(group)
        effective.update(aliases.get(group, set()))
    return sorted(effective)


class GroupAliasStore:
    """Postgres store for the identity-group to connector-group edges, one per tenant."""

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

    async def aliases_for_tenant(self, tenant_id: str) -> dict[str, set[str]]:
        """The alias map for a tenant: identity group -> set of connector groups. Used at
        query time to expand a subject's effective groups."""
        conn = await self._conn()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT identity_group, connector_group FROM pansoph_group_aliases "
                    "WHERE tenant_id = %s",
                    (tenant_id,),
                )
                rows = await cur.fetchall()
        finally:
            await conn.close()
        out: dict[str, set[str]] = {}
        for identity_group, connector_group in rows:
            out.setdefault(identity_group, set()).add(connector_group)
        return out

    async def list_aliases(self, tenant_id: str) -> list[dict[str, str]]:
        """Every alias for a tenant, for the admin UI, newest first."""
        conn = await self._conn()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT identity_group, connector_group FROM pansoph_group_aliases "
                    "WHERE tenant_id = %s ORDER BY created_at DESC",
                    (tenant_id,),
                )
                rows = await cur.fetchall()
        finally:
            await conn.close()
        return [{"identity_group": ig, "connector_group": cg} for ig, cg in rows]

    async def add_alias(self, tenant_id: str, identity_group: str, connector_group: str) -> None:
        """Declare that members of `identity_group` are also members of `connector_group`.
        Idempotent (the primary key makes a repeat a no op)."""
        conn = await self._conn()
        try:
            await conn.execute(
                "INSERT INTO pansoph_group_aliases (tenant_id, identity_group, connector_group) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (tenant_id, identity_group, connector_group),
            )
        finally:
            await conn.close()

    async def remove_alias(self, tenant_id: str, identity_group: str, connector_group: str) -> None:
        """Remove an alias. A revocation: the connector group is no longer expanded for the
        identity group, so its documents stop being reachable through that edge."""
        conn = await self._conn()
        try:
            await conn.execute(
                "DELETE FROM pansoph_group_aliases WHERE tenant_id = %s "
                "AND identity_group = %s AND connector_group = %s",
                (tenant_id, identity_group, connector_group),
            )
        finally:
            await conn.close()

    async def connector_groups_in_use(self, tenant_id: str) -> list[str]:
        """The distinct connector groups actually referenced by the tenant's indexed
        documents, so the admin picks a real target instead of guessing a group id. Read
        from the per document ACL log's allow_groups arrays."""
        conn = await self._conn()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT DISTINCT jsonb_array_elements_text(acl->'allow_groups') "
                    "FROM pansoph_doc_acls WHERE tenant_id = %s",
                    (tenant_id,),
                )
                rows = await cur.fetchall()
        finally:
            await conn.close()
        return sorted(r[0] for r in rows)
