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

from pansoph_authz import AuthzPort, ConsistencyToken, tuples_for_acl
from pansoph_connector_sdk import Connector
from pansoph_retrieval.ports import Embedder, SearchHit, VectorStore
from pansoph_types import DocRef, Document

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
        llm: LLM | None = None,
        contextualize: bool = False,
    ) -> None:
        self._connector = connector
        self._parser = parser
        self._embedder = embedder
        self._vector_store = vector_store
        self._authz = authz
        self._llm = llm
        self._contextualize = contextualize and llm is not None

    @property
    def connector(self) -> Connector:
        """The connector this pipeline syncs. Used by the durable workflow wrapper."""
        return self._connector

    async def sync(self, *, connector_name: str, tenant_id: str, cursor: str | None = None) -> str:
        """Sync everything changed since `cursor`. Returns the next cursor."""
        next_cursor = cursor or ""
        async for ref, cur in self._connector.list_changes(cursor):
            await self.ingest_one(connector_name=connector_name, tenant_id=tenant_id, ref=ref)
            next_cursor = cur
        return next_cursor

    async def ingest_one(
        self, *, connector_name: str, tenant_id: str, ref: DocRef
    ) -> ConsistencyToken:
        """Ingest a single document. A separately retryable, idempotent unit."""
        raw, acl = await self._connector.fetch(ref)
        parsed = await self._parser.parse(raw)
        chunks = chunk_sections(list(parsed.sections))
        if not chunks:
            return ConsistencyToken("")

        doc_id = Document.make_id(connector_name, ref.instance_id, ref.source_doc_id)
        embed_texts = await self._embed_texts(parsed.title, chunks)
        vectors = await self._embedder.embed(embed_texts)

        hits = [
            SearchHit(
                doc_id=doc_id,
                chunk_id=f"{doc_id}#{i}",
                score=0.0,
                tenant_id=tenant_id,
                allow_groups=list(acl.allow_groups),
                acl_version=1,
                text=chunk,
            )
            for i, chunk in enumerate(chunks)
        ]
        await self._vector_store.upsert(hits, vectors)
        return await self._authz.write_tuples(tuples_for_acl(doc_id, ref.instance_id, acl))

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
