"""Ingestion pipeline unit behavior."""

from __future__ import annotations

from _fakes import FakeAuthz, FakeVectorStore
from pansoph_connector_sdk import InMemoryConnector
from pansoph_ingestion.adapters_parser import DoclingParser
from pansoph_ingestion.pipeline import IngestionPipeline
from pansoph_types import AccessControl


class _RecordingEmbedder:
    model = "rec"
    dim = 3

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.seen.extend(texts)
        return [[1.0, 0.0, 0.0] for _ in texts]


class _ConstLLM:
    model = "const"

    async def complete(self, prompt: str, *, cache: bool = False) -> str:
        return "CONTEXT"


async def test_ingest_writes_hits_and_tuples_with_correct_metadata() -> None:
    connector = InMemoryConnector()
    connector.put("doc", "alpha\nbeta", AccessControl(allow_groups=["g1"]))
    store, authz = FakeVectorStore(), FakeAuthz()
    pipeline = IngestionPipeline(
        connector=connector,
        parser=DoclingParser(),
        embedder=_RecordingEmbedder(),
        vector_store=store,
        authz=authz,
    )
    await pipeline.sync(connector_name="memory", tenant_id="t1")

    assert store.rows, "expected at least one chunk indexed"
    hit = store.rows[0]
    assert hit.doc_id == "memory:mem:doc"
    assert hit.chunk_id.startswith("memory:mem:doc#")
    assert hit.tenant_id == "t1"
    assert hit.allow_groups == ["g1"]
    # The group reader tuple was written for this document, tenant-namespaced (the fake
    # renders principal ids as {ns}_{id} exactly like the real engine).
    assert ("group", "t1_g1", "memory:mem:doc") in authz.readers


async def test_contextualize_prepends_generated_context_to_embedding_input() -> None:
    connector = InMemoryConnector()
    connector.put("doc", "the body text", AccessControl(public=True))
    embedder = _RecordingEmbedder()
    pipeline = IngestionPipeline(
        connector=connector,
        parser=DoclingParser(),
        embedder=embedder,
        vector_store=FakeVectorStore(),
        authz=FakeAuthz(),
        llm=_ConstLLM(),
        contextualize=True,
    )
    await pipeline.sync(connector_name="memory", tenant_id="t1")
    assert any(t.startswith("CONTEXT") for t in embedder.seen)
    # The chunk text is still embedded after the context.
    assert any("the body text" in t for t in embedder.seen)


async def test_contextualize_off_embeds_raw_chunks() -> None:
    connector = InMemoryConnector()
    connector.put("doc", "plain chunk", AccessControl(public=True))
    embedder = _RecordingEmbedder()
    pipeline = IngestionPipeline(
        connector=connector,
        parser=DoclingParser(),
        embedder=embedder,
        vector_store=FakeVectorStore(),
        authz=FakeAuthz(),
    )
    await pipeline.sync(connector_name="memory", tenant_id="t1")
    assert embedder.seen == ["plain chunk"]
