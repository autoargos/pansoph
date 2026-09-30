"""The ingestion pipeline: the work scribe does for one connector instance.

For each changed document: fetch it with its ACL, parse, chunk by section,
optionally contextualize each chunk with a cached LLM call, embed, upsert to the
vector store with the ACL metadata, then write the authz tuples and record the
consistency token. The cursor is returned so the caller (the durable engine) can
checkpoint it.

This class depends only on ports, so it runs with real adapters in production and
with fakes in tests. The durable execution wrapper (DBOS) composes these same steps
so a crash resumes rather than restarts.
"""

from __future__ import annotations

import time

from pansoph_authz import AuthzPort, ConsistencyToken, diff_acls, narrow, tuples_for_acl
from pansoph_connector_sdk import Connector
from pansoph_retrieval.ports import Embedder, SearchHit, VectorStore
from pansoph_types import AccessControl, DocRef, Document

from .acl_log import AclLog
from .chunking import chunk_sections
from .ports import LLM, Parser

_CONTEXT_PROMPT = (
    "Here is a document titled {title}.\n\nHere is a chunk from it:\n{chunk}\n\n"
    "Give a short, standalone sentence situating this chunk within the document, "
    "so it can be retrieved on its own. Reply with only that sentence."
)


class IngestionPipeline:
    def __init__(
        self,
        *,
        connector: Connector,
        parser: Parser,
        embedder: Embedder,
        vector_store: VectorStore,
        authz: AuthzPort,
        acl_log: AclLog | None = None,
        llm: LLM | None = None,
        contextualize: bool = False,
    ) -> None:
        self._connector = connector
        self._parser = parser
        self._embedder = embedder
        self._vector_store = vector_store
        self._authz = authz
        # The last-written ACL per document, for revocation diffing. When absent, the
        # pipeline falls back to a blind full write (the pre-Phase-2 behavior), which
        # still adds grants but cannot detect a contraction. Production wires it.
        self._acl_log = acl_log
        self._llm = llm
        self._contextualize = contextualize and llm is not None

    @property
    def connector(self) -> Connector:
        """The connector this pipeline syncs. Used by the durable workflow wrapper."""
        return self._connector

    async def sync(
        self,
        *,
        connector_name: str,
        tenant_id: str,
        cursor: str | None = None,
        scope_type: str = "org",
        scope_ref: str = "",
    ) -> str:
        """Sync everything changed since `cursor`. Returns the next cursor.

        A tombstone (a DocRef with deleted=True) is a removal: the document is purged
        from the vector store and its reader tuples deleted, so a source deletion stops
        the document being retrievable. Every other ref is an upsert.
        """
        next_cursor = cursor or ""
        async for ref, cur in self._connector.list_changes(cursor):
            if ref.deleted:
                await self.delete_one(connector_name=connector_name, ref=ref)
            else:
                await self.ingest_one(
                    connector_name=connector_name,
                    tenant_id=tenant_id,
                    ref=ref,
                    scope_type=scope_type,
                    scope_ref=scope_ref,
                )
            next_cursor = cur
        return next_cursor

    async def ingest_one(
        self,
        *,
        connector_name: str,
        tenant_id: str,
        ref: DocRef,
        scope_type: str = "org",
        scope_ref: str = "",
    ) -> ConsistencyToken:
        """Ingest a single document. A separately retryable, idempotent unit."""
        raw, acl = await self._connector.fetch(ref)
        # An instance scope narrows the source ACL (never widens it), so a scoped
        # instance genuinely guards its documents even when the source is more open.
        acl = narrow(acl, scope_type, scope_ref)
        parsed = await self._parser.parse(raw)
        chunks = chunk_sections(list(parsed.sections))
        doc_id = Document.make_id(connector_name, ref.instance_id, ref.source_doc_id)
        if not chunks:
            # Nothing retrievable remains (emptied, or no longer parseable). Purge the
            # previous version and its grants rather than returning early, which kept the
            # old chunks and silently dropped any revocation in this version (CM-H2).
            await self.delete_one(connector_name=connector_name, ref=ref)
            return ConsistencyToken("")

        embed_texts = await self._embed_texts(parsed.title, chunks)
        vectors = await self._embedder.embed(embed_texts)

        # Reconcile authz tuples first so the chunks can store the consistency token of
        # this exact ACL write, which retrieval threads back into the bulk check. Users
        # and groups are both namespaced by tenant (a person is one principal per tenant),
        # so a direct-user grant resolves against the tenant-scoped query subject.
        token, acl_version = await self._apply_acl(doc_id, ref.instance_id, tenant_id, acl)
        written_at = time.time()
        hits = [
            SearchHit(
                doc_id=doc_id,
                chunk_id=f"{doc_id}#{i}",
                score=0.0,
                tenant_id=tenant_id,
                allow_groups=list(acl.allow_groups),
                deny=list(acl.deny),
                acl_version=acl_version,
                text=chunk,
                consistency_token=str(token),
                acl_written_at=written_at,
            )
            for i, chunk in enumerate(chunks)
        ]
        # Drop every previous chunk first: a shorter new version would otherwise leave its
        # old trailing chunks (text removed at the source) retrievable (CM-H3).
        await self._vector_store.delete_by_doc_id(doc_id)
        await self._vector_store.upsert(hits, vectors)
        return token

    async def _apply_acl(
        self, doc_id: str, instance_id: str, tenant_id: str, new_acl: AccessControl
    ) -> tuple[ConsistencyToken, int]:
        """Reconcile the authz tuples for a document to its new (narrowed) ACL and return
        the freshest consistency token plus the monotonic acl_version.

        Without an AclLog, this is the legacy blind write: it adds every grant but
        cannot see a revocation. With one, it diffs against the last ACL written, deletes
        the revoked tuples FIRST (the dangerous contraction), then writes the added ones,
        and bumps acl_version only when the ACL actually changed, so cache invalidation
        keyed on acl_version is meaningful.
        """
        new_tuples = tuples_for_acl(doc_id, instance_id, new_acl, tenant_id=tenant_id)
        if self._acl_log is None:
            token = await self._authz.write_tuples(new_tuples)
            return token, 1

        prior = await self._acl_log.get(doc_id)
        if prior is None:
            token = await self._authz.write_tuples(new_tuples)
            version = 1
        elif prior.acl == new_acl:
            # Unchanged ACL: rewrite (idempotent) to refresh the token, keep the version.
            token = await self._authz.write_tuples(new_tuples)
            version = prior.acl_version
        else:
            to_write, to_delete = diff_acls(
                doc_id, instance_id, prior.acl, new_acl, tenant_id=tenant_id
            )
            # Revocations first: a stale read must never see a removed grant.
            if to_delete:
                token = await self._authz.delete_tuples(to_delete)
            if to_write:
                token = await self._authz.write_tuples(to_write)
            if not to_delete and not to_write:
                token = await self._authz.write_tuples(new_tuples)
            version = prior.acl_version + 1

        await self._acl_log.put(doc_id, new_acl, tenant_id, version)
        return token, version

    async def delete_one(self, *, connector_name: str, ref: DocRef) -> None:
        """Purge a tombstoned document: remove its chunks from the vector store and delete
        its reader tuples, so a source deletion stops it being retrievable. Idempotent, so
        a replayed deletion step is safe. Content is never fetched for a tombstone.
        """
        doc_id = Document.make_id(connector_name, ref.instance_id, ref.source_doc_id)
        await self._vector_store.delete_by_doc_id(doc_id)
        if self._acl_log is not None:
            prior = await self._acl_log.get(doc_id)
            if prior is not None:
                # Reconstruct exactly the tuples written last time (same tenant
                # namespacing) so the delete matches what SpiceDB holds.
                tuples = tuples_for_acl(
                    doc_id, ref.instance_id, prior.acl, tenant_id=prior.tenant_id
                )
                if tuples:
                    await self._authz.delete_tuples(tuples)
                await self._acl_log.delete(doc_id)

    async def _embed_texts(self, title: str | None, chunks: list[str]) -> list[str]:
        """Optionally prepend generated context to each chunk before embedding."""
        if not self._contextualize or self._llm is None:
            return chunks
        out: list[str] = []
        for chunk in chunks:
            prompt = _CONTEXT_PROMPT.format(title=title or "(untitled)", chunk=chunk)
            context = await self._llm.complete(prompt, cache=True)
            out.append(f"{context.strip()}\n\n{chunk}")
        return out


async def purge_documents(
    *,
    prefix: str,
    vector_store: VectorStore,
    authz: AuthzPort,
    acl_log: AclLog,
) -> int:
    """Purge every document whose id starts with `prefix` (a connector instance's
    `{connector}:{instance_id}:`): its chunks, its reader tuples, and its ACL record.

    Used when a connector instance is deleted, so its content stops being retrievable
    instead of outliving the instance (CM-H5). The ACL log is the index of what was
    written, so the exact tuples are reconstructed and removed. Returns the count purged.
    """
    list_ids = getattr(acl_log, "doc_ids_with_prefix", None)
    if list_ids is None:
        raise NotImplementedError("the configured AclLog cannot list documents by prefix")
    parts = prefix.split(":")
    if len(parts) < 3 or not parts[0] or not parts[1]:
        raise ValueError(f"purge prefix must be '{{connector}}:{{instance}}:', got {prefix!r}")
    instance_id = parts[1]
    doc_ids: list[str] = await list_ids(prefix)
    for doc_id in doc_ids:
        await vector_store.delete_by_doc_id(doc_id)
        prior = await acl_log.get(doc_id)
        if prior is not None:
            tuples = tuples_for_acl(doc_id, instance_id, prior.acl, tenant_id=prior.tenant_id)
            if tuples:
                await authz.delete_tuples(tuples)
        await acl_log.delete(doc_id)
    return len(doc_ids)
