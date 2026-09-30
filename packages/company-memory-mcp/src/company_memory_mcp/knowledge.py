"""Adding, listing, and deleting manually supplied Company Memory knowledge.

Flow for `add`: validate the submission, decide the ACL from the verified caller and the
publish policy (never from arguments), store the record in the manual source, then index
it with the ordinary `IngestionPipeline.ingest_one`, which writes the authz tuples (and the
ACL log) before the chunks. Retrieval is unchanged: the new document is found only through
`retrieve()` and `get_source`, under the same SpiceDB check as every other document.

Defaults are safe: no scope means private to the creator. A shared scope needs an
explicit publish grant (see publish_policy.py); reading a group is not writing to it.

Submitted text is untrusted data. It is stored and indexed as text: nothing in it is
executed, no path in it is opened, no URL in it is fetched, and nothing is deserialized.
Audit events record who, tenant, tool, decision, the document id, the requested scope, and
the effective ACL, never the content.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from pansoph_authz import Principal
from pansoph_core import assembly
from pansoph_core.audit import audit
from pansoph_core.routes import resolve_subject
from pansoph_ingestion.pipeline import IngestionPipeline
from pansoph_types import AccessControl, DocRef

from . import knowledge_store as ks
from .publish_policy import PolicyError, load_policy
from .service import Caller

MAX_CONTENT_BYTES = 200_000
MAX_TITLE_CHARS = 200
MAX_DESCRIPTION_CHARS = 1000
MAX_TAGS = 10
MAX_FILENAME_CHARS = 200
_TAG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_SOURCE_DOC_ID = re.compile(r"^k[0-9a-f]{24}$")
PRIVATE = "private"
NOT_MANAGEABLE = "Knowledge not found or not manageable by you."
ADD_TOOL = "add_company_memory_knowledge"


class SubmissionError(ValueError):
    """A submission is malformed or too large. The message is safe to show the caller."""


def _clean_line(value: str | None, field: str, limit: int, required: bool = False) -> str:
    text = " ".join((value or "").split())
    if required and not text:
        raise SubmissionError(f"{field} must not be empty")
    if len(text) > limit:
        raise SubmissionError(f"{field} is longer than {limit} characters")
    return text


def _clean_content(content: str) -> str:
    if not isinstance(content, str) or not content.strip():
        raise SubmissionError("content must not be empty")
    size = len(content.encode("utf-8"))
    if size > MAX_CONTENT_BYTES:
        raise SubmissionError(
            f"content is {size} bytes; the limit is {MAX_CONTENT_BYTES} bytes. Split the "
            "document into smaller parts and add each one."
        )
    return content.strip()


def _clean_tags(tags: list[str] | str | None) -> list[str]:
    if isinstance(tags, str):
        tags = tags.split(",")
    out: list[str] = []
    for tag in tags or []:
        t = str(tag).strip().lower()
        if not _TAG.match(t):
            raise SubmissionError(f"bad tag {tag!r}: use lowercase letters, digits, and dashes")
        if t not in out:
            out.append(t)
    if len(out) > MAX_TAGS:
        raise SubmissionError(f"at most {MAX_TAGS} tags")
    return out


def _clean_filename(name: str | None) -> str:
    """Keep only a base name, as a label. The server never opens it."""
    base = PureWindowsPath(PurePosixPath(name or "").name).name.strip()
    base = "".join(ch for ch in base if ch.isprintable())
    return base[:MAX_FILENAME_CHARS]


def _parse_scope(scope: str | None) -> str | None:
    """None for private, else the requested group name (not yet authorized)."""
    raw = (scope or "").strip().lower()
    if raw in ("", PRIVATE, "me", "self"):
        return None
    group = raw[len("group:") :] if raw.startswith("group:") else raw
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,63}$", group):
        raise SubmissionError("scope must be 'private' or a group name such as 'project-atlas'")
    return group


def private_acl(principal: str) -> AccessControl:
    """The ACL of knowledge added without a scope: the creator, and nobody else."""
    return AccessControl(allow_users=[principal])


def _source_doc_id(caller: Caller, scope: str, title: str, content: str) -> str:
    """Stable and deterministic: the same creator adding the same document to the same
    scope gets the same id (a retry is idempotent). Different creators never collide."""
    material = "\x00".join((caller.tenant_id, caller.principal, scope, title, content))
    return "k" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _pipeline(store: ks.KnowledgeStore, tenant_id: str) -> IngestionPipeline:
    return IngestionPipeline(
        connector=ks.ManualKnowledgeConnector(store, tenant_id),
        parser=assembly.build_parser(),
        embedder=assembly.build_embedder(),
        vector_store=assembly.build_vector_store(),
        authz=assembly.build_authz(),
        acl_log=assembly.build_acl_log(),
    )


def _acl_summary(acl: AccessControl) -> dict[str, Any]:
    return {"allow_groups": list(acl.allow_groups), "allow_users": list(acl.allow_users)}


def _audit_add(caller: Caller, decision: str, **fields: Any) -> None:
    audit(
        "mcp_knowledge_write",
        actor=caller.principal,
        tenant=caller.tenant_id,
        tool=ADD_TOOL,
        decision=decision,
        **fields,
    )


async def add(
    caller: Caller,
    *,
    title: str,
    content: str,
    description: str | None = None,
    tags: list[str] | str | None = None,
    scope: str | None = None,
    original_filename: str | None = None,
    mcp_client: str = "",
    store: ks.KnowledgeStore | None = None,
) -> dict[str, Any]:
    requested = (scope or PRIVATE).strip()[:80]
    try:
        clean_title = _clean_line(title, "title", MAX_TITLE_CHARS, required=True)
        body = _clean_content(content)
        clean_desc = _clean_line(description, "description", MAX_DESCRIPTION_CHARS)
        clean_tags = _clean_tags(tags)
        group = _parse_scope(scope)
    except SubmissionError as exc:
        _audit_add(caller, "rejected", reason=str(exc), requested_scope=requested)
        return {"status": "rejected", "error": str(exc)}

    if group is None:
        scope_label = PRIVATE
        acl = private_acl(caller.principal)
    else:
        scope_label = f"group:{group}"
        try:
            policy = load_policy()
        except PolicyError:
            _audit_add(
                caller, "denied", reason="publish policy unavailable", requested_scope=requested
            )
            return {"status": "denied", "error": "Shared publishing is unavailable right now."}
        decision = policy.decide(
            tenant_id=caller.tenant_id, caller_groups=list(caller.groups), scope=group
        )
        if not decision.allowed:
            _audit_add(caller, "denied", reason=decision.reason, requested_scope=requested)
            return {
                "status": "denied",
                "error": f"You are not authorized to publish to '{group}'.",
                "publishable_scopes": [
                    PRIVATE,
                    *policy.publishable_for(caller.tenant_id, list(caller.groups)),
                ],
            }
        acl = AccessControl(allow_groups=list(decision.read_groups))

    store = store or ks.build_knowledge_store()
    source_doc_id = _source_doc_id(caller, scope_label, clean_title, body)
    now = datetime.now(UTC)
    existing = await store.get(caller.tenant_id, source_doc_id)
    record = ks.KnowledgeRecord(
        source_doc_id=source_doc_id,
        tenant_id=caller.tenant_id,
        created_by=caller.principal,
        created_at=existing.created_at if existing else now,
        # Same id means same creator, scope, title, and content: an identical re-add keeps
        # the record unchanged (idempotent), and only re-indexes it.
        updated_at=existing.updated_at if existing else now,
        title=clean_title,
        content=body,
        description=clean_desc,
        tags=clean_tags,
        scope=scope_label,
        acl=acl,
        content_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        mcp_client="".join(ch for ch in " ".join(mcp_client.split()) if ch.isprintable())[:120],
        original_filename=_clean_filename(original_filename),
    )
    await store.put(record)
    # The normal pansoph path: fetch from the manual source, parse, chunk, embed, ACL
    # tuples (revocations first), ACL log, then the chunks.
    pipeline = _pipeline(store, caller.tenant_id)
    try:
        await pipeline.ingest_one(
            connector_name=ks.CONNECTOR,
            tenant_id=caller.tenant_id,
            ref=DocRef(source_doc_id=source_doc_id, instance_id=ks.INSTANCE),
        )
    except Exception:
        # Leave nothing half indexed: purge whatever was written, and drop a new record.
        await pipeline.delete_one(
            connector_name=ks.CONNECTOR,
            ref=DocRef(source_doc_id=source_doc_id, instance_id=ks.INSTANCE, deleted=True),
        )
        if existing is None:
            await store.delete(caller.tenant_id, source_doc_id)
        _audit_add(caller, "error", source_id=record.doc_id, requested_scope=requested)
        raise
    chunks = await _chunk_count(record.doc_id, caller.tenant_id)
    status = "unchanged" if existing else "added"
    _audit_add(
        caller,
        "allowed",
        source_id=record.doc_id,
        requested_scope=requested,
        effective_acl=_acl_summary(acl),
        status=status,
        content_bytes=len(body.encode("utf-8")),
        content_sha256=record.content_sha256[:16],
        chunks=chunks,
    )
    audience = (
        "only you"
        if group is None
        else (
            f"readers of '{group}': members of group(s) {', '.join(acl.allow_groups)} in "
            f"tenant {caller.tenant_id} (checked on every read)"
        )
    )
    return {
        "status": status,
        "source_id": record.doc_id,
        "title": clean_title,
        "scope": scope_label,
        "visible_to": audience,
        "chunks_indexed": chunks,
        "created_by": caller.principal,
        "created_at": record.created_at.isoformat(),
    }


async def _chunk_count(doc_id: str, tenant_id: str) -> int:
    read = getattr(assembly.build_vector_store(), "get_document_chunks", None)
    return len(await read(doc_id, tenant_id)) if read is not None else -1


async def readable(caller: Caller, doc_ids: list[str]) -> set[str]:
    """The subset of doc ids the caller may read now (the same SpiceDB check retrieval
    uses, at a token at least as fresh as this request's membership write)."""
    if not doc_ids:
        return set()
    authz = assembly.build_authz()
    _, fresh = await resolve_subject(authz, caller.principal, caller.groups, caller.tenant_id)
    allowed: set[str] = await authz.bulk_check(
        subject=Principal(type="user", id=caller.principal, ns=caller.tenant_id),
        permission="read",
        object_ids=doc_ids,
        at_least_as_fresh=fresh,
    )
    return allowed


async def list_mine(
    caller: Caller, limit: int | None = 20, store: ks.KnowledgeStore | None = None
) -> dict[str, Any]:
    store = store or ks.build_knowledge_store()
    mine = await store.list_by_creator(caller.tenant_id, caller.principal)
    allowed = await readable(caller, [r.doc_id for r in mine])
    n = max(1, min(int(limit or 20), 50))
    items = [
        {
            "source_id": r.doc_id,
            "title": r.title,
            "scope": r.scope,
            "description": r.description,
            "tags": r.tags,
            "created_at": r.created_at.isoformat(),
        }
        for r in mine
        if r.doc_id in allowed
    ][:n]
    audit(
        "mcp_tool",
        actor=caller.principal,
        tenant=caller.tenant_id,
        tool="list_my_company_memory_knowledge",
        decision="allowed",
        returned=[i["source_id"] for i in items],
    )
    return {"principal": caller.principal, "knowledge": items}


def manual_source_doc_id(source_id: str) -> str | None:
    prefix = f"{ks.CONNECTOR}:{ks.INSTANCE}:"
    if not isinstance(source_id, str) or not source_id.startswith(prefix):
        return None
    rest = source_id[len(prefix) :]
    return rest if _SOURCE_DOC_ID.match(rest) else None


async def _may_manage(caller: Caller, record: ks.KnowledgeRecord) -> bool:
    """Only the creator manages a manual document. For a shared one the creator must also
    still hold the publish grant for its scope, so leaving a project ends the right."""
    if record.tenant_id != caller.tenant_id or record.created_by != caller.principal:
        return False
    if record.scope == PRIVATE:
        return True
    try:
        policy = load_policy()
    except PolicyError:
        return False
    group = record.scope[len("group:") :]
    return policy.decide(
        tenant_id=caller.tenant_id, caller_groups=list(caller.groups), scope=group
    ).allowed


async def delete(
    caller: Caller, source_id: str, store: ks.KnowledgeStore | None = None
) -> dict[str, Any]:
    store = store or ks.build_knowledge_store()
    sdi = manual_source_doc_id(source_id)
    record = await store.get(caller.tenant_id, sdi) if sdi else None
    ok = record is not None and await _may_manage(caller, record)
    if not ok or record is None:
        audit(
            "mcp_knowledge_write",
            actor=caller.principal,
            tenant=caller.tenant_id,
            tool="delete_company_memory_knowledge",
            decision="denied",
            source_id=source_id if isinstance(source_id, str) else "",
        )
        return {"status": "denied", "source_id": source_id, "error": NOT_MANAGEABLE}
    # Purge through the pipeline: chunks, the reader tuples of the last written ACL, and
    # the ACL log entry. Then the source record, so a failed purge can be retried.
    await _pipeline(store, caller.tenant_id).delete_one(
        connector_name=ks.CONNECTOR,
        ref=DocRef(source_doc_id=record.source_doc_id, instance_id=ks.INSTANCE, deleted=True),
    )
    await store.delete(caller.tenant_id, record.source_doc_id)
    audit(
        "mcp_knowledge_write",
        actor=caller.principal,
        tenant=caller.tenant_id,
        tool="delete_company_memory_knowledge",
        decision="allowed",
        source_id=record.doc_id,
        effective_acl=_acl_summary(record.acl),
    )
    return {"status": "deleted", "source_id": record.doc_id}


async def provenance_for(
    caller: Caller, doc_ids: list[str], store: ks.KnowledgeStore | None = None
) -> dict[str, dict[str, Any]]:
    """Provenance for manual documents the caller has ALREADY been authorized to read.
    The caller of this function must pass only authorized ids."""
    out: dict[str, dict[str, Any]] = {}
    manual = [(d, manual_source_doc_id(d)) for d in doc_ids]
    if not any(sdi for _, sdi in manual):
        return out
    store = store or ks.build_knowledge_store()
    for doc_id, sdi in manual:
        if sdi is None:
            continue
        record = await store.get(caller.tenant_id, sdi)
        if record is not None:
            out[doc_id] = {"title": record.title, **record.provenance()}
    return out
