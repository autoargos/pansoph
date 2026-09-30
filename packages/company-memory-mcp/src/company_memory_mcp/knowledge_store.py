"""The manual knowledge source: records for knowledge added through the MCP write tool.

A record is the source of truth for one manually added document, the same role a Drive
file plays for the Drive connector. The ingestion pipeline reads it through
`ManualKnowledgeConnector` and indexes it like any other source document, so chunks,
authz tuples, and the ACL log are written by the normal pansoph path, never here.

Every field that decides identity or access (`created_by`, `tenant_id`, `acl`) is set by
the server from the verified caller, never from tool arguments. Reads are always scoped
to a tenant. The store never authorizes a read: callers run the authz check first.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Protocol

from pansoph_types import (
    AccessControl,
    AuthScheme,
    Capabilities,
    DocRef,
    RawDoc,
    ResourcePage,
)
from pydantic import BaseModel, Field

CONNECTOR = "cm_manual"
INSTANCE = "manual"
SOURCE_TYPE = "manual_mcp"


class KnowledgeRecord(BaseModel):
    source_doc_id: str
    tenant_id: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    title: str
    content: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    scope: str  # "private" or "group:<name>"
    acl: AccessControl
    content_sha256: str
    mcp_client: str = ""
    original_filename: str = ""
    source_type: str = SOURCE_TYPE

    @property
    def doc_id(self) -> str:
        return f"{CONNECTOR}:{INSTANCE}:{self.source_doc_id}"

    def provenance(self) -> dict[str, Any]:
        """What an authorized reader may see about where this knowledge came from."""
        return {
            "source_type": self.source_type,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "scope": self.scope,
            "description": self.description,
            "tags": list(self.tags),
            "original_filename": self.original_filename,
            "mcp_client": self.mcp_client,
            "trust": "user_supplied",
        }


class KnowledgeStore(Protocol):
    async def get(self, tenant_id: str, source_doc_id: str) -> KnowledgeRecord | None: ...
    async def put(self, record: KnowledgeRecord) -> None: ...
    async def delete(self, tenant_id: str, source_doc_id: str) -> None: ...
    async def list_by_creator(self, tenant_id: str, created_by: str) -> list[KnowledgeRecord]: ...
    async def all_records(self) -> list[KnowledgeRecord]: ...


class InMemoryKnowledgeStore:
    """Process local store for tests."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str], KnowledgeRecord] = {}

    async def get(self, tenant_id: str, source_doc_id: str) -> KnowledgeRecord | None:
        return self.records.get((tenant_id, source_doc_id))

    async def put(self, record: KnowledgeRecord) -> None:
        self.records[(record.tenant_id, record.source_doc_id)] = record

    async def delete(self, tenant_id: str, source_doc_id: str) -> None:
        self.records.pop((tenant_id, source_doc_id), None)

    async def list_by_creator(self, tenant_id: str, created_by: str) -> list[KnowledgeRecord]:
        found = [
            r
            for r in self.records.values()
            if r.tenant_id == tenant_id and r.created_by == created_by
        ]
        return sorted(found, key=lambda r: r.created_at, reverse=True)

    async def all_records(self) -> list[KnowledgeRecord]:
        return list(self.records.values())


_DDL = """
CREATE TABLE IF NOT EXISTS cm_manual_knowledge (
    tenant_id      text NOT NULL,
    source_doc_id  text NOT NULL,
    created_by     text NOT NULL,
    record         jsonb NOT NULL,
    created_at     timestamptz NOT NULL,
    updated_at     timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, source_doc_id)
);
CREATE INDEX IF NOT EXISTS cm_manual_knowledge_creator
    ON cm_manual_knowledge (tenant_id, created_by, created_at DESC);
"""


class PostgresKnowledgeStore:
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

    async def _rows(self, sql: str, params: tuple[Any, ...]) -> list[KnowledgeRecord]:
        conn = await self._conn()
        try:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        finally:
            await conn.close()
        return [KnowledgeRecord.model_validate(r[0]) for r in rows]

    async def get(self, tenant_id: str, source_doc_id: str) -> KnowledgeRecord | None:
        rows = await self._rows(
            "SELECT record FROM cm_manual_knowledge WHERE tenant_id = %s AND source_doc_id = %s",
            (tenant_id, source_doc_id),
        )
        return rows[0] if rows else None

    async def put(self, record: KnowledgeRecord) -> None:
        conn = await self._conn()
        try:
            await conn.execute(
                "INSERT INTO cm_manual_knowledge (tenant_id, source_doc_id, created_by, record, "
                "created_at, updated_at) VALUES (%s, %s, %s, %s::jsonb, %s, %s) "
                "ON CONFLICT (tenant_id, source_doc_id) DO UPDATE SET "
                "record = EXCLUDED.record, updated_at = EXCLUDED.updated_at",
                (
                    record.tenant_id,
                    record.source_doc_id,
                    record.created_by,
                    json.dumps(record.model_dump(mode="json")),
                    record.created_at,
                    record.updated_at,
                ),
            )
        finally:
            await conn.close()

    async def delete(self, tenant_id: str, source_doc_id: str) -> None:
        conn = await self._conn()
        try:
            await conn.execute(
                "DELETE FROM cm_manual_knowledge WHERE tenant_id = %s AND source_doc_id = %s",
                (tenant_id, source_doc_id),
            )
        finally:
            await conn.close()

    async def list_by_creator(self, tenant_id: str, created_by: str) -> list[KnowledgeRecord]:
        return await self._rows(
            "SELECT record FROM cm_manual_knowledge WHERE tenant_id = %s AND created_by = %s "
            "ORDER BY created_at DESC LIMIT 200",
            (tenant_id, created_by),
        )

    async def all_records(self) -> list[KnowledgeRecord]:
        return await self._rows("SELECT record FROM cm_manual_knowledge", ())


def build_knowledge_store() -> KnowledgeStore:
    from pansoph_core import assembly

    return PostgresKnowledgeStore(assembly._postgres_dsn())


class ManualKnowledgeConnector:
    """The manual source as a pansoph connector, so records go through the ordinary
    ingestion pipeline (parse, chunk, embed, ACL diff, tuples, upsert).

    Not registered as an entry point: instances are never configured through the admin
    API, only driven by the MCP write tool. `fetch` returns the stored text with the
    stored ACL; nothing is resolved from a path or a URL.
    """

    def __init__(self, store: KnowledgeStore, tenant_id: str) -> None:
        from pansoph_connector_sdk import ConnectorMeta

        self.instance_id = INSTANCE
        self._store = store
        self._tenant_id = tenant_id
        self.meta = ConnectorMeta(
            name=CONNECTOR,
            auth=AuthScheme.none,
            capabilities=Capabilities(incremental=False, permission_sync=True, deletions=True),
        )

    def validate(self, config: dict[str, Any]) -> Any:
        from pansoph_connector_sdk import ValidationResult

        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        """Every record of this tenant (a full re-index). Deletions are applied directly by
        the delete tool, so no tombstones are needed here."""
        for record in await self._store.all_records():
            if record.tenant_id == self._tenant_id:
                yield DocRef(source_doc_id=record.source_doc_id, instance_id=INSTANCE), ""

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        record = await self._store.get(self._tenant_id, ref.source_doc_id)
        if record is None:
            raise LookupError("manual knowledge record not found")
        raw = RawDoc(
            source_doc_id=record.source_doc_id,
            instance_id=INSTANCE,
            mime="text/markdown",
            text=f"Title: {record.title}\n\n{record.content}",
            title=record.title,
            extra={"source_type": SOURCE_TYPE, "created_by": record.created_by},
        )
        return raw, record.acl

    async def list_acls(self, ref: DocRef) -> AccessControl:
        _, acl = await self.fetch(ref)
        return acl

    async def on_webhook(self, event: dict[str, Any]) -> list[Any]:
        return []

    async def list_resources(
        self,
        kind: str | None = None,
        parent_id: str | None = None,
        cursor: str | None = None,
    ) -> ResourcePage:
        return ResourcePage()
