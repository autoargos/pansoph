"""Local demo state administration, run INSIDE the Company Memory MCP container.

    docker compose exec -T mcp python -m company_memory_mcp.demo_state inspect
    docker compose exec -T mcp python -m company_memory_mcp.demo_state reset < plan.json

Postgres and SpiceDB are not reachable from the host (by design), so the host CLI's
`demo-reset` and `demo-check` drive this module through `docker compose exec`, where the
container already holds its datastore credentials. Nothing here is an API: there is no
network entry point, and it refuses to run unless the container declares itself the local
POC (`CM_DEMO_ENV=local-poc`, set only in deploy/company-memory/compose.yaml).

`reset` removes manually added knowledge generically (every `cm_manual` document, found by
its source type, except the ids the plan keeps), through the ordinary pipeline purge
(chunks, reader tuples, ACL log), then sweeps orphans (manual chunks or ACL records with no
source record), and reconciles each known subject's SpiceDB memberships to its baseline
groups exactly as a query would. Memberships of subjects no longer known (temporary
verification users) are deleted. Synthetic corpus documents are never touched.

`inspect` is read only: it reports counts, the manual records, their ACLs, the loaded
publish policy, and the container's startup guard verdict. It prints no secrets or text.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pansoph_authz import TENANT_EVERYONE, Principal, membership_tuples
from pansoph_types import DocRef

from . import knowledge_store as ks

DEMO_ENV = "CM_DEMO_ENV"
DEMO_ENV_VALUE = "local-poc"
MANUAL_PREFIX = f"{ks.CONNECTOR}:{ks.INSTANCE}:"


class NotDemoEnvironment(RuntimeError):
    pass


def require_demo_environment(env: dict[str, str] | None = None) -> None:
    e = dict(os.environ) if env is None else env
    if e.get(DEMO_ENV) != DEMO_ENV_VALUE:
        raise NotDemoEnvironment(
            f"refusing: this is not the Company Memory local demo ({DEMO_ENV} is not "
            f"{DEMO_ENV_VALUE!r})"
        )


@dataclass
class DemoPorts:
    store: Any
    vector_store: Any
    authz: Any
    acl_log: Any
    list_manual_chunk_doc_ids: Callable[[], Awaitable[list[str]]]
    list_membership_rows: Callable[[], Awaitable[list[tuple[str, str, set[str]]]]]
    delete_membership_row: Callable[[str, str], Awaitable[None]]


@dataclass(frozen=True)
class Subject:
    subject: str
    tenant: str
    groups: tuple[str, ...]


def _pipeline(ports: DemoPorts, tenant_id: str) -> Any:
    from pansoph_core import assembly
    from pansoph_ingestion.pipeline import IngestionPipeline

    return IngestionPipeline(
        connector=ks.ManualKnowledgeConnector(ports.store, tenant_id),
        parser=assembly.build_parser(),
        embedder=assembly.build_embedder(),
        vector_store=ports.vector_store,
        authz=ports.authz,
        acl_log=ports.acl_log,
    )


def _ref(doc_id: str) -> DocRef:
    return DocRef(source_doc_id=doc_id[len(MANUAL_PREFIX) :], instance_id=ks.INSTANCE, deleted=True)


async def reset(ports: DemoPorts, *, keep: set[str], subjects: list[Subject]) -> dict[str, Any]:
    removed: list[str] = []
    # 1. Every manual source record not kept: purge its index, then the record.
    for record in await ports.store.all_records():
        if record.doc_id in keep:
            continue
        await _pipeline(ports, record.tenant_id).delete_one(
            connector_name=ks.CONNECTOR, ref=_ref(record.doc_id)
        )
        await ports.store.delete(record.tenant_id, record.source_doc_id)
        removed.append(record.doc_id)
    # 2. Orphans: manual ACL records or chunks whose source record is gone.
    records = {r.doc_id for r in await ports.store.all_records()}
    orphans = 0
    for doc_id in await ports.acl_log.doc_ids_with_prefix(MANUAL_PREFIX):
        if doc_id not in records:
            await _pipeline(ports, "").delete_one(connector_name=ks.CONNECTOR, ref=_ref(doc_id))
            orphans += 1
    for doc_id in await ports.list_manual_chunk_doc_ids():
        if doc_id not in records:
            await ports.vector_store.delete_by_doc_id(doc_id)
            orphans += 1
    # 3. Memberships: unknown subjects lose theirs; known ones get exactly their baseline
    #    (the same reconcile a query runs, so no stale temporary grant survives).
    known = {(s.tenant, s.subject) for s in subjects}
    rows = await ports.list_membership_rows()
    forgotten = await forget(ports, [(t, sub) for t, sub, _ in rows if (t, sub) not in known])
    from pansoph_core.routes import resolve_subject

    for s in subjects:
        await resolve_subject(ports.authz, s.subject, list(s.groups), s.tenant)
    return {
        "removed_manual_documents": sorted(removed),
        "removed_orphans": orphans,
        "forgotten_subjects": forgotten,
        "reconciled_subjects": len(subjects),
        "kept": sorted(keep & records),
    }


async def forget(ports: DemoPorts, subjects: list[tuple[str, str]]) -> int:
    """Delete the SpiceDB memberships and the membership record of (tenant, subject)
    pairs, e.g. a temporary verification user that no longer exists. Idempotent."""
    wanted = set(subjects)
    count = 0
    for tenant, subject, groups in await ports.list_membership_rows():
        if (tenant, subject) not in wanted:
            continue
        if groups:
            await ports.authz.delete_tuples(membership_tuples(subject, sorted(groups), tenant))
        await ports.delete_membership_row(tenant, subject)
        count += 1
    return count


def private_default_ok() -> bool:
    """The code path for knowledge without a scope still yields a creator only ACL."""
    from .knowledge import _parse_scope, private_acl

    acl = private_acl("probe@example.com")
    return (
        all(_parse_scope(v) is None for v in (None, "", "private"))
        and acl.allow_users == ["probe@example.com"]
        and not acl.allow_groups
        and acl.public is False
    )


async def inspect(ports: DemoPorts) -> dict[str, Any]:
    """Read only. What the demo check needs to know about stored state."""
    from .guards import unsafe_config_reasons
    from .publish_policy import PolicyError, load_policy

    manual = []
    for record in sorted(await ports.store.all_records(), key=lambda r: r.doc_id):
        logged = await ports.acl_log.get(record.doc_id)
        chunks = await ports.vector_store.get_document_chunks(record.doc_id, record.tenant_id)
        acl = logged.acl if logged is not None else None
        manual.append(
            {
                "doc_id": record.doc_id,
                "tenant": record.tenant_id,
                "title": record.title,
                "scope": record.scope,
                "created_by": record.created_by,
                "chunks": len(chunks),
                "indexed_acl": None
                if acl is None
                else {
                    "public": acl.public,
                    "allow_groups": sorted(acl.allow_groups),
                    "allow_users": sorted(acl.allow_users),
                },
                "text_has_marker": {
                    m: any(m in (c.text or "") for c in chunks) for m in ("NORTHSTAR",)
                },
            }
        )
    try:
        policy = load_policy()
        scopes = {
            tenant: {
                name: {
                    "read_groups": list(rule.read_groups),
                    "publisher_groups": sorted(rule.publisher_groups),
                }
                for name, rule in rules.items()
            }
            for tenant, rules in policy.scopes.items()
        }
        policy_error = ""
    except PolicyError as exc:
        scopes, policy_error = {}, str(exc)
    # Reachability of the engine, with a check that grants nothing (read only).
    spicedb_ok = True
    try:
        await ports.authz.bulk_check(
            subject=Principal(type="user", id="demo-check-nobody", ns="demo-check"),
            permission="read",
            object_ids=["demo-check:none"],
        )
    except Exception:  # noqa: BLE001  reported, not raised
        spicedb_ok = False
    return {
        "demo_env": os.environ.get(DEMO_ENV, ""),
        "guard_reasons": unsafe_config_reasons(),
        "policy": scopes,
        "policy_error": policy_error,
        "spicedb_ok": spicedb_ok,
        "private_default_ok": private_default_ok(),
        "manual": manual,
        "orphan_chunk_doc_ids": sorted(
            set(await ports.list_manual_chunk_doc_ids()) - {m["doc_id"] for m in manual}
        ),
        "orphan_acl_doc_ids": sorted(
            set(await ports.acl_log.doc_ids_with_prefix(MANUAL_PREFIX))
            - {m["doc_id"] for m in manual}
        ),
        "tenant_everyone_group": TENANT_EVERYONE,
    }


# ---------------------------------------------------------------- production wiring


def production_ports() -> DemoPorts:
    from pansoph_core import assembly

    dsn = assembly._postgres_dsn()

    async def _query(sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        import psycopg

        conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
        try:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                return list(await cur.fetchall()) if cur.description else []
        finally:
            await conn.close()

    async def chunk_doc_ids() -> list[str]:
        rows = await _query(
            "SELECT DISTINCT doc_id FROM pansoph_chunks WHERE doc_id LIKE %s",
            (MANUAL_PREFIX.replace("_", "\\_") + "%",),
        )
        return sorted(str(r[0]) for r in rows)

    async def membership_rows() -> list[tuple[str, str, set[str]]]:
        # Touch the store first so its table exists on a fresh database.
        await assembly.build_membership_store().get("demo-check", "demo-check")
        rows = await _query("SELECT tenant_id, subject, groups FROM pansoph_subject_memberships")
        return [(str(t), str(s), set(g or [])) for t, s, g in rows]

    async def delete_row(tenant: str, subject: str) -> None:
        await _query(
            "DELETE FROM pansoph_subject_memberships WHERE tenant_id = %s AND subject = %s",
            (tenant, subject),
        )

    return DemoPorts(
        store=ks.build_knowledge_store(),
        vector_store=assembly.build_vector_store(),
        authz=assembly.build_authz(),
        acl_log=assembly.build_acl_log(),
        list_manual_chunk_doc_ids=chunk_doc_ids,
        list_membership_rows=membership_rows,
        delete_membership_row=delete_row,
    )


def _plan_from_stdin() -> tuple[set[str], list[Subject]]:
    plan = json.loads(sys.stdin.read() or "{}")
    keep = {str(k) for k in plan.get("keep", []) if str(k).startswith(MANUAL_PREFIX)}
    subjects = [
        Subject(str(s["subject"]), str(s["tenant"]), tuple(str(g) for g in s["groups"]))
        for s in plan.get("subjects", [])
    ]
    return keep, subjects


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["guards"]:
        # Only this container's startup guard verdict (no demo marker needed to report it).
        from .guards import unsafe_config_reasons

        sys.stdout.write(json.dumps({"guard_reasons": unsafe_config_reasons()}))
        return
    require_demo_environment()
    if args[:1] == ["inspect"]:
        out = asyncio.run(inspect(production_ports()))
    elif args[:1] == ["forget"]:
        plan = json.loads(sys.stdin.read() or "{}")
        pairs = [(str(s["tenant"]), str(s["subject"])) for s in plan.get("subjects", [])]
        out = {"forgotten_subjects": asyncio.run(forget(production_ports(), pairs))}
    elif args[:1] == ["reset"]:
        keep, subjects = _plan_from_stdin()
        out = asyncio.run(reset(production_ports(), keep=keep, subjects=subjects))
    else:
        raise SystemExit(
            "usage: python -m company_memory_mcp.demo_state inspect|reset|forget|guards"
        )
    sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    main()
