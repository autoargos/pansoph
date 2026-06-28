"""Connector instance management and the sync runner.

A connector instance is a configured use of a connector type (e.g. one GitHub repo),
with its own name, tenant, config, schedule, and cursor. Instances and their sync
run history live in Postgres so the dashboard can show health and timings. The sync
runner drives a connector through the ingestion pipeline and records the run.

Modular by construction: the connector is resolved through the entry-point registry,
the pipeline depends only on ports, and nothing here is connector-specific.

Note: connector config is stored as JSON. For instances that carry secrets (a token),
those should be sealed through the SecretStore port in production; the public-repo
demo carries no secret. This is a documented follow-up, not a silent gap.
"""

from __future__ import annotations

import time
from typing import Any

from pansoph_ingestion.pipeline import IngestionPipeline

from . import assembly
from .registry import load_adapter

_DDL = """
CREATE TABLE IF NOT EXISTS connector_instances (
    id               text PRIMARY KEY,
    connector        text NOT NULL,
    name             text NOT NULL,
    tenant_id        text NOT NULL DEFAULT 'default',
    config           jsonb NOT NULL DEFAULT '{}',
    schedule_seconds integer NOT NULL DEFAULT 0,
    enabled          boolean NOT NULL DEFAULT true,
    cursor           text,
    created_at       timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE connector_instances ADD COLUMN IF NOT EXISTS schedule_cron text;
-- An instance's scope: which slice of a source it covers (org-wide, one group's, or
-- one individual's), and an optional reference (a group id or user id). First-class
-- so the admin UI can present and filter by it and role checks can key off it.
ALTER TABLE connector_instances ADD COLUMN IF NOT EXISTS scope_type text NOT NULL DEFAULT 'org';
ALTER TABLE connector_instances ADD COLUMN IF NOT EXISTS scope_ref text;
CREATE TABLE IF NOT EXISTS sync_runs (
    id            bigserial PRIMARY KEY,
    instance_id   text NOT NULL,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    status        text NOT NULL DEFAULT 'running',
    docs_indexed  integer NOT NULL DEFAULT 0,
    error         text
);
CREATE INDEX IF NOT EXISTS sync_runs_instance ON sync_runs (instance_id, started_at DESC);
"""

# The granularity an instance covers. Orthogonal to the source ACL: scope is a
# management concept (who owns and administers this instance), the source ACL still
# governs who may read its documents.
_SCOPE_TYPES = {"org", "group", "individual"}


def validate_connector_config(connector: str, instance_id: str, config: dict[str, Any]) -> None:
    """Validate a config against the connector's own contract before persisting, so a
    misconfigured instance fails at creation rather than mid-sync. Resolves the
    connector through the registry (never importing it here), instantiates it, and
    calls its validate. Raises ValueError on an unknown connector or an invalid config.
    """
    try:
        probe = load_adapter("pansoph.connectors", connector)(instance_id, config)
    except Exception as exc:  # noqa: BLE001  surface a clear creation-time error
        raise ValueError(f"unknown or unloadable connector '{connector}': {exc}") from exc
    validate = getattr(probe, "validate", None)
    if validate is None:
        return
    result = validate(config)
    if not getattr(result, "ok", True):
        raise ValueError(result.error or "invalid connector config")


class ConnectorManager:
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

    # --- instances ---
    async def create_instance(
        self,
        *,
        instance_id: str,
        connector: str,
        name: str,
        tenant_id: str,
        config: dict[str, Any],
        schedule_seconds: int,
        schedule_cron: str = "",
        scope_type: str = "org",
        scope_ref: str = "",
    ) -> dict[str, Any]:
        from psycopg.types.json import Json

        validate_connector_config(connector, instance_id, config)
        if scope_type not in _SCOPE_TYPES:
            raise ValueError(f"scope_type must be one of {sorted(_SCOPE_TYPES)}")

        conn = await self._conn()
        try:
            await conn.execute(
                """
                INSERT INTO connector_instances
                  (id, connector, name, tenant_id, config, schedule_seconds, schedule_cron,
                   scope_type, scope_ref)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                  connector = EXCLUDED.connector, name = EXCLUDED.name,
                  tenant_id = EXCLUDED.tenant_id, config = EXCLUDED.config,
                  schedule_seconds = EXCLUDED.schedule_seconds,
                  schedule_cron = EXCLUDED.schedule_cron,
                  scope_type = EXCLUDED.scope_type, scope_ref = EXCLUDED.scope_ref
                """,
                (
                    instance_id,
                    connector,
                    name,
                    tenant_id,
                    Json(config),
                    schedule_seconds,
                    schedule_cron or None,
                    scope_type,
                    scope_ref or None,
                ),
            )
        finally:
            await conn.close()
        return await self.get_instance(instance_id)  # type: ignore[return-value]

    async def delete_instance(self, instance_id: str) -> None:
        conn = await self._conn()
        try:
            await conn.execute("DELETE FROM connector_instances WHERE id = %s", (instance_id,))
            await conn.execute("DELETE FROM sync_runs WHERE instance_id = %s", (instance_id,))
        finally:
            await conn.close()

    async def get_instance(self, instance_id: str) -> dict[str, Any] | None:
        conn = await self._conn()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, connector, name, tenant_id, config, schedule_seconds, "
                    "schedule_cron, enabled, cursor, created_at, scope_type, scope_ref "
                    "FROM connector_instances WHERE id = %s",
                    (instance_id,),
                )
                row = await cur.fetchone()
        finally:
            await conn.close()
        return self._instance_row(row) if row else None

    async def list_instances(self) -> list[dict[str, Any]]:
        conn = await self._conn()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, connector, name, tenant_id, config, schedule_seconds, "
                    "schedule_cron, enabled, cursor, created_at, scope_type, scope_ref "
                    "FROM connector_instances ORDER BY created_at"
                )
                rows = await cur.fetchall()
                out = []
                for row in rows:
                    inst = self._instance_row(row)
                    await cur.execute(
                        "SELECT started_at, finished_at, status, docs_indexed, error "
                        "FROM sync_runs WHERE instance_id = %s ORDER BY started_at DESC LIMIT 1",
                        (inst["id"],),
                    )
                    last = await cur.fetchone()
                    inst["last_run"] = self._run_row(last) if last else None
                    out.append(inst)
                return out
        finally:
            await conn.close()

    async def set_cursor(self, instance_id: str, cursor: str | None) -> None:
        conn = await self._conn()
        try:
            await conn.execute(
                "UPDATE connector_instances SET cursor = %s WHERE id = %s", (cursor, instance_id)
            )
        finally:
            await conn.close()

    # --- runs ---
    async def record_run_start(self, instance_id: str) -> int:
        conn = await self._conn()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO sync_runs (instance_id, status) "
                    "VALUES (%s, 'running') RETURNING id",
                    (instance_id,),
                )
                return (await cur.fetchone())[0]  # type: ignore[index]
        finally:
            await conn.close()

    async def record_run_finish(
        self, run_id: int, status: str, docs: int, error: str | None
    ) -> None:
        conn = await self._conn()
        try:
            await conn.execute(
                "UPDATE sync_runs SET finished_at = now(), status = %s, docs_indexed = %s, "
                "error = %s WHERE id = %s",
                (status, docs, error, run_id),
            )
        finally:
            await conn.close()

    async def runs_for(self, instance_id: str, limit: int = 20) -> list[dict[str, Any]]:
        conn = await self._conn()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT started_at, finished_at, status, docs_indexed, error "
                    "FROM sync_runs WHERE instance_id = %s ORDER BY started_at DESC LIMIT %s",
                    (instance_id, limit),
                )
                return [self._run_row(r) for r in await cur.fetchall()]
        finally:
            await conn.close()

    # --- row mappers ---
    @staticmethod
    def _instance_row(row: Any) -> dict[str, Any]:
        return {
            "id": row[0],
            "connector": row[1],
            "name": row[2],
            "tenant_id": row[3],
            "config": row[4],
            "schedule_seconds": row[5],
            "schedule_cron": row[6],
            "enabled": row[7],
            "cursor": row[8],
            "created_at": row[9].isoformat() if row[9] else None,
            "scope_type": row[10],
            "scope_ref": row[11],
        }

    @staticmethod
    def _run_row(row: Any) -> dict[str, Any]:
        started = row[0]
        finished = row[1]
        duration = (finished - started).total_seconds() if finished and started else None
        return {
            "started_at": started.isoformat() if started else None,
            "finished_at": finished.isoformat() if finished else None,
            "status": row[2],
            "docs_indexed": row[3],
            "error": row[4],
            "duration_seconds": duration,
        }


async def run_instance(manager: ConnectorManager, instance: dict[str, Any]) -> dict[str, Any]:
    """Run one connector instance through the ingestion pipeline and record the run."""
    run_id = await manager.record_run_start(instance["id"])
    parser = assembly.build_parser()
    embedder = assembly.build_embedder()
    vector = assembly.build_vector_store()
    authz = assembly.build_authz()
    pipeline = IngestionPipeline(
        connector=_build_connector(instance),
        parser=parser,
        embedder=embedder,
        vector_store=vector,
        authz=authz,
    )
    count = 0
    next_cursor = instance.get("cursor")
    try:
        async for ref, cur in pipeline.connector.list_changes(instance.get("cursor")):
            await pipeline.ingest_one(
                connector_name=instance["connector"], tenant_id=instance["tenant_id"], ref=ref
            )
            count += 1
            next_cursor = cur
        await manager.record_run_finish(run_id, "success", count, None)
        await manager.set_cursor(instance["id"], next_cursor)
        return {"status": "success", "docs_indexed": count}
    except Exception as exc:  # noqa: BLE001  record the failure, surface it to the caller
        await manager.record_run_finish(run_id, "error", count, str(exc))
        return {"status": "error", "docs_indexed": count, "error": str(exc)}


def _build_connector(instance: dict[str, Any]) -> Any:
    cls = load_adapter("pansoph.connectors", instance["connector"])
    connector = cls(instance["id"], instance["config"])
    _attach_auth(connector, instance["tenant_id"])
    return connector


def _attach_auth(connector: Any, tenant_id: str, provider: Any = None) -> None:
    """Give a connector a refreshing TokenSource if it declares an AuthRequirement.

    Backward compatible by construction: a connector that does not expose
    auth_requirement (or whose auth_requirement returns None) is left untouched and
    keeps using its own config-based auth, so this cannot break an existing
    connector. A connector that opts in receives connector.token_source, bound to the
    configured secret store and the process-wide credential provider, and calls
    get_access_token() per request. The provider is injectable for tests.
    """
    get_req = getattr(connector, "auth_requirement", None)
    if get_req is None:
        return
    requirement = get_req()
    if requirement is None:
        return
    if provider is None:
        provider = assembly.build_credential_provider()
    connector.token_source = provider.token_source(tenant_id, requirement)


async def health(dsn: str) -> dict[str, Any]:
    """Best-effort reachability of the core dependencies, for the dashboard."""
    checks: dict[str, Any] = {}

    async def _timed(name: str, coro: Any) -> None:
        start = time.monotonic()
        try:
            await coro
            checks[name] = {"ok": True, "latency_ms": round((time.monotonic() - start) * 1000, 1)}
        except Exception as exc:  # noqa: BLE001  health probe never raises
            checks[name] = {"ok": False, "error": str(exc)[:200]}

    import psycopg

    async def _pg() -> None:
        conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()

    await _timed("postgres", _pg())
    return {"ok": all(c.get("ok") for c in checks.values()), "checks": checks}
