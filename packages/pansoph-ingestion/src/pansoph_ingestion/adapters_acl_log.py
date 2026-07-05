"""Postgres adapter for the AclLog port.

A small key-value table keyed by doc_id holds the last ACL pansoph wrote for each
document and its monotonic acl_version. It is read once per document on ingest to
diff against the new ACL (revocation detection) and written after each successful
tuple write. It is deleted when a document is purged. The table is intentionally
tiny (one row per document, JSON ACL plus an integer version), and never scanned, so
it adds no hot-path cost.
"""

from __future__ import annotations

from typing import Any

from pansoph_types import AccessControl

from .acl_log import AclRecord

_DDL = """
CREATE TABLE IF NOT EXISTS pansoph_doc_acls (
    doc_id      text PRIMARY KEY,
    acl         jsonb NOT NULL,
    tenant_id   text NOT NULL DEFAULT '',
    acl_version integer NOT NULL DEFAULT 1,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
"""


class PostgresAclLog:
    """Durable AclLog over Postgres. Satisfies the AclLog port."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._ready = False

    async def _connect(self) -> Any:
        import psycopg

        conn = await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)
        if not self._ready:
            await conn.execute(_DDL)
            self._ready = True
        return conn

    async def get(self, doc_id: str) -> AclRecord | None:
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT acl, tenant_id, acl_version FROM pansoph_doc_acls WHERE doc_id = %s",
                    (doc_id,),
                )
                row = await cur.fetchone()
        finally:
            await conn.close()
        if row is None:
            return None
        return AclRecord(AccessControl.model_validate(row[0]), str(row[1]), int(row[2]))

    async def put(self, doc_id: str, acl: AccessControl, tenant_id: str, acl_version: int) -> None:
        from psycopg.types.json import Json

        conn = await self._connect()
        try:
            await conn.execute(
                """
                INSERT INTO pansoph_doc_acls (doc_id, acl, tenant_id, acl_version, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (doc_id) DO UPDATE SET
                  acl = EXCLUDED.acl,
                  tenant_id = EXCLUDED.tenant_id,
                  acl_version = EXCLUDED.acl_version,
                  updated_at = EXCLUDED.updated_at
                """,
                (doc_id, Json(acl.model_dump()), tenant_id, acl_version),
            )
        finally:
            await conn.close()

    async def delete(self, doc_id: str) -> None:
        conn = await self._connect()
        try:
            await conn.execute("DELETE FROM pansoph_doc_acls WHERE doc_id = %s", (doc_id,))
        finally:
            await conn.close()
