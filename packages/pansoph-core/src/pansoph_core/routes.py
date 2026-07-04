"""agora HTTP routes: search, chat (SSE), and discovery.

The chat stream follows the shape of the AI SDK UI Message Stream: source parts
first, then text deltas, then a finish event, all over SSE. lens consumes this, but
the contract is the wire format, not a library, so any frontend can.

Adapters are built per request from configuration. For a hot service these would be
cached on app state; built per request here for clarity. Subject and groups come
from the request for now; wiring them to the Identity port and a session is the next
step. Retrieval still enforces permissions through the authz bulk check regardless.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pansoph_authz import TENANT_EVERYONE, Principal, membership_tuples, tuples_for_acl
from pansoph_ingestion.chunking import chunk_sections
from pansoph_retrieval.pipeline import retrieve
from pansoph_retrieval.ports import SearchHit
from pansoph_types import AccessControl, Document, RawDoc
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import assembly
from .audit import audit
from .connectors import ConnectorManager, health, run_instance
from .registry import list_adapters, load_adapter

router = APIRouter(prefix="/api")

_PORT_GROUPS = [
    "pansoph.connectors",
    "pansoph.embedders",
    "pansoph.rerankers",
    "pansoph.llms",
    "pansoph.vectorstores",
    "pansoph.authz",
    "pansoph.parsers",
    "pansoph.identity",
    "pansoph.objectstores",
    "pansoph.secretstores",
]


class Query(BaseModel):
    query: str
    # user and groups are the fallback identity when no bearer token is present
    # (the demo switcher). With a token they are ignored in favour of the token.
    user: str = "anonymous"
    groups: list[str] = []
    tenant_id: str = "default"
    final_k: int = 8


async def _identity_from_token(authorization: str | None) -> Any:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    try:
        return await assembly.build_identity().authenticate(token)
    except Exception:  # noqa: BLE001  an invalid token is anonymous, never a 500
        return None


async def _effective_identity(req: Query, authorization: str | None) -> tuple[str, list[str]]:
    """Prefer the validated token identity; fall back to the request body (the demo
    switcher) when no bearer token is present. The authz check enforces either way."""
    user = await _identity_from_token(authorization)
    if user is not None:
        return user.id, list(user.group_ids)
    return req.user, req.groups


async def _require_identity(authorization: str | None = Header(None)) -> Any:
    """Require a valid bearer token. Gates the write surface that any signed-in user
    may use (e.g. grab their own content).

    This is authentication: a request with no valid token is rejected. The check
    delegates to the Identity port, so it is adapter agnostic, never coupled to a
    specific IdP."""
    user = await _identity_from_token(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


# Canonical pansoph management roles. Orthogonal to groups (which gate document
# retrieval). The token carries these as a flat `roles` claim; any IdP maps its own
# roles onto these strings via its claim mapper, so nothing here is IdP-specific.
ROLE_ADMIN = "admin"
ROLE_CONNECTOR_ADMIN = "connector_admin"
ROLE_MEMBER = "member"


def has_required_role(user_roles: list[str] | None, allowed: tuple[str, ...]) -> bool:
    """The pure authorization decision: admin always passes, otherwise the caller
    must hold at least one allowed role. Kept separate so it is unit testable without
    a token or an IdP."""
    roles = set(user_roles or [])
    return ROLE_ADMIN in roles or bool(roles.intersection(allowed))


def require_role(*allowed: str) -> Any:
    """A dependency that requires the caller to hold one of `allowed` roles (admin
    always passes). Authentication plus authorization, both through the Identity
    port. Returns the authenticated user."""

    async def _dep(authorization: str | None = Header(None)) -> Any:
        user = await _identity_from_token(authorization)
        if user is None:
            raise HTTPException(status_code=401, detail="authentication required")
        if has_required_role(getattr(user, "roles", []), allowed):
            return user
        raise HTTPException(
            status_code=403, detail=f"requires one of roles: {', '.join((ROLE_ADMIN, *allowed))}"
        )

    return _dep


# Dependency singletons, built once so they are plain names in argument defaults.
_CONNECTOR_ADMIN = require_role(ROLE_CONNECTOR_ADMIN)


async def _retrieve(
    user: str, groups: list[str], tenant_id: str, query: str, final_k: int
) -> list[Any]:
    authz = assembly.build_authz()
    # Sync the subject's identity groups into the authz graph so the engine can
    # resolve nested-group document access. Idempotent. In a full deployment this is
    # done by the Identity port on login or SCIM sync; here it is per request.
    # Every user is a member of its tenant's everyone group, so a public document
    # (granted to that group, not a global wildcard) is readable only within its
    # tenant. This enforces tenant isolation at the authz layer, defense in depth with
    # the vector pre-filter. Identity groups are synced alongside it.
    await authz.write_tuples(membership_tuples(user, [TENANT_EVERYONE, *groups], tenant_id))
    return await retrieve(
        query=query,
        subject=Principal(type="user", id=user),
        tenant_id=tenant_id,
        subject_groups=groups,
        final_k=final_k,
        vector_store=assembly.build_vector_store(),
        embedder=assembly.build_embedder(),
        authz=authz,
        reranker=assembly.build_reranker(),
    )


@router.post("/search")
async def search(req: Query, authorization: str | None = Header(None)) -> dict[str, Any]:
    user, groups = await _effective_identity(req, authorization)
    hits = await _retrieve(user, groups, req.tenant_id, req.query, req.final_k)
    audit("search", actor=user, tenant=req.tenant_id, results=len(hits))
    return {
        "results": [
            {"doc_id": h.doc_id, "chunk_id": h.chunk_id, "score": h.score, "text": h.text}
            for h in hits
        ]
    }


@router.post("/chat")
async def chat(req: Query, authorization: str | None = Header(None)) -> EventSourceResponse:
    user, groups = await _effective_identity(req, authorization)
    hits = await _retrieve(user, groups, req.tenant_id, req.query, req.final_k)
    audit("chat", actor=user, tenant=req.tenant_id, sources=len(hits))
    llm = assembly.build_llm()

    if hits:
        context = "\n\n".join(f"[{i + 1}] {h.text}" for i, h in enumerate(hits))
        prompt = (
            "Answer the question using only the sources below. Cite sources inline "
            "as [n]. If the sources do not contain the answer, say so.\n\n"
            f"Sources:\n{context}\n\nQuestion: {req.query}\n\nAnswer:"
        )
    else:
        # Honest omission: no permitted sources, answer from world knowledge, no citations.
        prompt = req.query

    async def gen() -> AsyncIterator[dict[str, str]]:
        for i, h in enumerate(hits):
            yield {
                "data": json.dumps(
                    {"type": "source", "id": i + 1, "doc_id": h.doc_id, "chunk_id": h.chunk_id}
                )
            }
        answer = await llm.complete(prompt)
        for word in answer.split(" "):
            yield {"data": json.dumps({"type": "text-delta", "delta": word + " "})}
        yield {"data": json.dumps({"type": "finish"})}

    return EventSourceResponse(gen())


class GrabRequest(BaseModel):
    url: str
    title: str | None = None
    text: str
    tenant_id: str = "default"
    instance_id: str = "grabber"
    public: bool = False
    allow_groups: list[str] = []


@router.post("/grab")
async def grab(req: GrabRequest, user: Any = Depends(_require_identity)) -> dict[str, Any]:
    """Ingest content captured by the scout extension: what the signed in user can
    already see, with consent. Same indexing path as a connector sync, so the
    captured page is immediately retrievable under the ACL the request sets.

    Requires a valid token: the captured content is attributed to the authenticated
    user, not to a caller-supplied id, so the audit trail is trustworthy."""
    parser = assembly.build_parser()
    embedder = assembly.build_embedder()
    vector = assembly.build_vector_store()
    authz = assembly.build_authz()

    raw = RawDoc(
        source_doc_id=req.url,
        instance_id=req.instance_id,
        mime="text/html",
        text=req.text,
        title=req.title,
        source_url=req.url,
    )
    parsed = await parser.parse(raw)
    chunks = chunk_sections(list(parsed.sections))
    if not chunks:
        return {"indexed": 0}

    doc_id = Document.make_id("scout", req.instance_id, req.url)
    vectors = await embedder.embed(chunks)
    # Group the captured doc under the tenant namespace, so identity groups (also
    # tenant-namespaced) resolve against it.
    acl = AccessControl(
        public=req.public, allow_groups=req.allow_groups, principal_ns=req.tenant_id
    )
    hits = [
        SearchHit(
            doc_id=doc_id,
            chunk_id=f"{doc_id}#{i}",
            score=0.0,
            tenant_id=req.tenant_id,
            allow_groups=list(acl.allow_groups),
            deny=list(acl.deny),
            acl_version=1,
            text=chunk,
        )
        for i, chunk in enumerate(chunks)
    ]
    await vector.upsert(hits, vectors)
    await authz.write_tuples(tuples_for_acl(doc_id, req.instance_id, acl, tenant_id=req.tenant_id))
    audit("grab", actor=user.id, tenant=req.tenant_id, url=req.url, chunks=len(chunks))
    return {"indexed": len(chunks), "doc_id": doc_id}


@router.get("/connectors")
async def connectors() -> dict[str, list[str]]:
    return {"available": list_adapters("pansoph.connectors")}


def _serialize_field(f: Any) -> dict[str, Any]:
    """Serialize one ConfigField to the UI contract. FieldKind is a StrEnum, so its
    value is the lowercase wire string (string, int, bool, list, secret, ...)."""
    out: dict[str, Any] = {
        "key": f.key,
        "label": f.label,
        "kind": f.kind.value,
        "required": f.required,
    }
    # Optional hints are only included when the connector declared them, keeping the
    # payload small and the form renderer free to skip absent keys.
    if f.help:
        out["help"] = f.help
    if f.placeholder:
        out["placeholder"] = f.placeholder
    return out


def _serialize_spec(name: str, spec: Any) -> dict[str, Any]:
    """Serialize a ConnectorSpec to the UI contract. `name` is the registry name (the
    entry point key), which is what the create-instance call keys off, so it is used
    rather than spec.name in case the two ever diverge."""
    return {
        "name": name,
        "title": spec.title,
        "description": spec.description,
        "fields": [_serialize_field(f) for f in spec.fields],
    }


def _spec_for(name: str) -> Any | None:
    """Load the connector class for `name` and return its declared ConnectorSpec, or
    None if the connector does not declare a spec() classmethod."""
    cls = load_adapter("pansoph.connectors", name)
    spec_fn = getattr(cls, "spec", None)
    if spec_fn is None:
        return None
    return spec_fn()


@router.get("/connectors/specs")
async def connector_specs() -> dict[str, list[dict[str, Any]]]:
    """All connector config specs, so the UI can render the add-connector form
    dynamically instead of a hardcoded map. Public metadata, unauthenticated like the
    plain /connectors list. Connectors without a spec() are skipped, and the result is
    sorted by human title."""
    specs: list[dict[str, Any]] = []
    for name in list_adapters("pansoph.connectors"):
        spec = _spec_for(name)
        if spec is None:
            continue
        specs.append(_serialize_spec(name, spec))
    specs.sort(key=lambda s: s["title"])
    return {"connectors": specs}


@router.get("/connectors/{name}/spec")
async def connector_spec(name: str) -> dict[str, Any]:
    """One connector's config spec. 404 when the name is unknown or the connector does
    not declare a spec(), so the UI can fall back gracefully."""
    try:
        spec = _spec_for(name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if spec is None:
        raise HTTPException(status_code=404, detail=f"connector {name!r} has no spec")
    return _serialize_spec(name, spec)


@router.get("/adapters")
async def adapters() -> dict[str, list[str]]:
    return {group: list_adapters(group) for group in _PORT_GROUPS}


# --- connector instance management ---


def _manager() -> ConnectorManager:
    return ConnectorManager(assembly._postgres_dsn())


class InstanceCreate(BaseModel):
    connector: str
    name: str
    instance_id: str
    tenant_id: str = "default"
    config: dict[str, Any] = {}
    schedule_seconds: int = 0
    schedule_cron: str = ""
    # Management scope: which slice of the source this instance covers.
    scope_type: str = "org"  # org | group | individual
    scope_ref: str = ""


@router.get("/connectors/instances")
async def list_instances() -> dict[str, Any]:
    return {"instances": await _manager().list_instances()}


@router.post("/connectors/instances")
async def create_instance(
    req: InstanceCreate, user: Any = Depends(_CONNECTOR_ADMIN)
) -> dict[str, Any]:
    try:
        return await _manager().create_instance(
            instance_id=req.instance_id,
            connector=req.connector,
            name=req.name,
            tenant_id=req.tenant_id,
            config=req.config,
            schedule_seconds=req.schedule_seconds,
            schedule_cron=req.schedule_cron,
            scope_type=req.scope_type,
            scope_ref=req.scope_ref,
        )
    except ValueError as exc:
        # A bad connector name, an invalid config, or an unknown scope type is the
        # caller's error, not a server fault.
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/connectors/instances/{instance_id}")
async def delete_instance(
    instance_id: str, user: Any = Depends(_CONNECTOR_ADMIN)
) -> dict[str, str]:
    await _manager().delete_instance(instance_id)
    return {"deleted": instance_id}


@router.post("/connectors/instances/{instance_id}/sync")
async def sync_instance(instance_id: str, user: Any = Depends(_CONNECTOR_ADMIN)) -> dict[str, Any]:
    manager = _manager()
    instance = await manager.get_instance(instance_id)
    if instance is None:
        return {"error": "instance not found"}
    result = await run_instance(manager, instance)
    audit("connector_sync", actor="system", tenant=instance["tenant_id"], instance=instance_id)
    return result


@router.get("/connectors/instances/{instance_id}/runs")
async def instance_runs(instance_id: str) -> dict[str, Any]:
    return {"runs": await _manager().runs_for(instance_id)}


@router.get("/health")
async def health_endpoint() -> dict[str, Any]:
    return await health(assembly._postgres_dsn())
