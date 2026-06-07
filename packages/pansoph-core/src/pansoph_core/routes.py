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
from pansoph_authz import Principal
from pansoph_retrieval.pipeline import retrieve
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import assembly
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
    return await retrieve(
        query=req.query,
        subject=Principal(type="user", id=req.user),
        tenant_id=req.tenant_id,
        subject_groups=req.groups,
        final_k=req.final_k,
        vector_store=assembly.build_vector_store(),
        embedder=assembly.build_embedder(),
        authz=assembly.build_authz(),
        reranker=assembly.build_reranker(),
    )


@router.post("/search")
async def search(req: Query) -> dict[str, Any]:
    hits = await _retrieve(req)
    return {
        "results": [
            {"doc_id": h.doc_id, "chunk_id": h.chunk_id, "score": h.score, "text": h.text}
            for h in hits
        ]
    }


@router.post("/chat")
async def chat(req: Query) -> EventSourceResponse:
    hits = await _retrieve(req)
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


@router.get("/connectors")
async def connectors() -> dict[str, list[str]]:
    return {"available": list_adapters("pansoph.connectors")}


@router.get("/adapters")
async def adapters() -> dict[str, list[str]]:
    return {group: list_adapters(group) for group in _PORT_GROUPS}
