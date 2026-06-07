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

from fastapi import APIRouter
from pansoph_authz import Principal, membership_tuples, tuples_for_acl
from pansoph_ingestion.chunking import chunk_sections
from pansoph_retrieval.pipeline import retrieve
from pansoph_retrieval.ports import SearchHit
from pansoph_types import AccessControl, Document, RawDoc
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import assembly
from .audit import audit
from .connectors import ConnectorManager, health, run_instance
from .registry import list_adapters

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
    user: str
    groups: list[str] = []
    tenant_id: str = "default"
    final_k: int = 8


async def _retrieve(req: Query) -> list[Any]:
    authz = assembly.build_authz()
    # Sync the subject's identity groups into the authz graph so the engine can
    # resolve nested-group document access. Idempotent. In a full deployment this is
    # done by the Identity port on login or SCIM sync; here it is per request.
    if req.groups:
        await authz.write_tuples(membership_tuples(req.user, req.groups, req.tenant_id))
    return await retrieve(
        query=req.query,
        subject=Principal(type="user", id=req.user),
        tenant_id=req.tenant_id,
        subject_groups=req.groups,
        final_k=req.final_k,
        vector_store=assembly.build_vector_store(),
        embedder=assembly.build_embedder(),
        authz=authz,
        reranker=assembly.build_reranker(),
    )


@router.post("/search")
async def search(req: Query) -> dict[str, Any]:
    hits = await _retrieve(req)
    audit("search", actor=req.user, tenant=req.tenant_id, results=len(hits))
    return {
        "results": [
            {"doc_id": h.doc_id, "chunk_id": h.chunk_id, "score": h.score, "text": h.text}
            for h in hits
        ]
    }


@router.post("/chat")
async def chat(req: Query) -> EventSourceResponse:
    hits = await _retrieve(req)
    audit("chat", actor=req.user, tenant=req.tenant_id, sources=len(hits))
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
async def grab(req: GrabRequest) -> dict[str, Any]:
    """Ingest content captured by the scout extension: what the signed in user can
    already see, with consent. Same indexing path as a connector sync, so the
    captured page is immediately retrievable under the ACL the request sets."""
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
            acl_version=1,
            text=chunk,
        )
        for i, chunk in enumerate(chunks)
    ]
    await vector.upsert(hits, vectors)
    await authz.write_tuples(tuples_for_acl(doc_id, req.instance_id, acl))
    audit("grab", actor=req.instance_id, tenant=req.tenant_id, url=req.url, chunks=len(chunks))
    return {"indexed": len(chunks), "doc_id": doc_id}


@router.get("/connectors")
async def connectors() -> dict[str, list[str]]:
    return {"available": list_adapters("pansoph.connectors")}


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


@router.get("/connectors/instances")
async def list_instances() -> dict[str, Any]:
    return {"instances": await _manager().list_instances()}


@router.post("/connectors/instances")
async def create_instance(req: InstanceCreate) -> dict[str, Any]:
    return await _manager().create_instance(
        instance_id=req.instance_id,
        connector=req.connector,
        name=req.name,
        tenant_id=req.tenant_id,
        config=req.config,
        schedule_seconds=req.schedule_seconds,
    )


@router.delete("/connectors/instances/{instance_id}")
async def delete_instance(instance_id: str) -> dict[str, str]:
    await _manager().delete_instance(instance_id)
    return {"deleted": instance_id}


@router.post("/connectors/instances/{instance_id}/sync")
async def sync_instance(instance_id: str) -> dict[str, Any]:
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
