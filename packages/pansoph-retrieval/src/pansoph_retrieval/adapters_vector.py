"""VectorStore adapters.

PgVectorScaleStore is the local and small tier: it keeps chunks, ACL metadata, and
both a dense embedding and a tsvector in one Postgres table, and fuses dense and
sparse results with reciprocal rank fusion. QdrantStore is the scale tier.

Both apply the coarse ACL pre-filter (tenant plus group overlap) for speed and
recall. They are not the source of truth; the authz bulk check downstream is. So
the pre-filter is deliberately generous: it keeps a chunk whose groups overlap the
subject's groups, or that has no groups at all, and the authz check removes anything
the subject may not actually read.

Both deps are optional: install pansoph-retrieval[pgvectorscale] or [qdrant].
"""

from __future__ import annotations

from typing import Any

from .ports import SearchHit, VectorFilter
from .rrf import reciprocal_rank_fusion

_DDL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS pansoph_chunks (
    chunk_id    text PRIMARY KEY,
    doc_id      text NOT NULL,
    tenant_id   text NOT NULL,
    allow_groups text[] NOT NULL DEFAULT '{}',
    acl_version int NOT NULL DEFAULT 0,
    body        text,
    embedding   vector(%(dim)s),
    tsv         tsvector
);
ALTER TABLE pansoph_chunks ADD COLUMN IF NOT EXISTS deny text[] NOT NULL DEFAULT '{}';
ALTER TABLE pansoph_chunks ADD COLUMN IF NOT EXISTS consistency_token text NOT NULL DEFAULT '';
ALTER TABLE pansoph_chunks ADD COLUMN IF NOT EXISTS acl_written_at double precision
    NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS pansoph_chunks_tenant ON pansoph_chunks (tenant_id);
CREATE INDEX IF NOT EXISTS pansoph_chunks_tsv ON pansoph_chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS pansoph_chunks_groups ON pansoph_chunks USING gin (allow_groups);
"""


class PgVectorScaleStore:
    def __init__(self, dsn: str, dim: int = 1024) -> None:
        self._dsn = dsn
        self._dim = dim
        self._ready = False

    async def _connect(self) -> Any:
        import psycopg

        conn = await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)
        if not self._ready:
            await conn.execute(_DDL % {"dim": self._dim})
            self._ready = True
        return conn

    async def upsert(self, hits: list[SearchHit], vectors: list[list[float]]) -> None:
        conn = await self._connect()
        try:
            async with conn.cursor() as cur:
                for hit, vec in zip(hits, vectors, strict=True):
                    await cur.execute(
                        """
                        INSERT INTO pansoph_chunks (
                            chunk_id, doc_id, tenant_id, allow_groups, deny,
                            acl_version, body, embedding, tsv,
                            consistency_token, acl_written_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, to_tsvector('english', %s),
                                %s, %s)
                        ON CONFLICT (chunk_id) DO UPDATE SET
                          doc_id = EXCLUDED.doc_id,
                          tenant_id = EXCLUDED.tenant_id,
                          allow_groups = EXCLUDED.allow_groups,
                          deny = EXCLUDED.deny,
                          acl_version = EXCLUDED.acl_version,
                          body = EXCLUDED.body,
                          embedding = EXCLUDED.embedding,
                          tsv = EXCLUDED.tsv,
                          consistency_token = EXCLUDED.consistency_token,
                          acl_written_at = EXCLUDED.acl_written_at
                        """,
                        (
                            hit.chunk_id,
                            hit.doc_id,
                            hit.tenant_id,
                            hit.allow_groups,
                            hit.deny,
                            hit.acl_version,
                            hit.text,
                            _vec_literal(vec),
                            hit.text or "",
                            hit.consistency_token,
                            hit.acl_written_at,
                        ),
                    )
        finally:
            await conn.close()

    async def hybrid_search(
        self, query_vector: list[float], query_text: str, flt: VectorFilter, limit: int
    ) -> list[SearchHit]:
        conn = await self._connect()
        try:
            # Cast the bound array to text[] so Postgres applies the overlap operator;
            # an untyped array parameter does not match.
            where = (
                "tenant_id = %(tenant)s AND "
                "(cardinality(allow_groups) = 0 OR allow_groups && %(groups)s::text[])"
            )
            params: dict[str, Any] = {
                "tenant": flt.tenant_id,
                "groups": flt.any_groups,
                "qv": _vec_literal(query_vector),
                "q": query_text,
                "k": limit,
            }
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT chunk_id FROM pansoph_chunks WHERE {where} "
                    "ORDER BY embedding <=> %(qv)s ASC LIMIT %(k)s",
                    params,
                )
                dense = [r[0] for r in await cur.fetchall()]
                await cur.execute(
                    "SELECT chunk_id FROM pansoph_chunks "
                    f"WHERE {where} AND tsv @@ plainto_tsquery('english', %(q)s) "
                    "ORDER BY ts_rank(tsv, plainto_tsquery('english', %(q)s)) DESC LIMIT %(k)s",
                    params,
                )
                sparse = [r[0] for r in await cur.fetchall()]

                fused = [cid for cid, _ in reciprocal_rank_fusion([dense, sparse])][:limit]
                if not fused:
                    return []
                await cur.execute(
                    "SELECT chunk_id, doc_id, tenant_id, allow_groups, acl_version, body, deny, "
                    "consistency_token, acl_written_at "
                    "FROM pansoph_chunks WHERE chunk_id = ANY(%s)",
                    (fused,),
                )
                by_id = {
                    row[0]: SearchHit(
                        chunk_id=row[0],
                        doc_id=row[1],
                        tenant_id=row[2],
                        allow_groups=list(row[3] or []),
                        acl_version=row[4],
                        text=row[5],
                        deny=list(row[6] or []),
                        consistency_token=row[7] or "",
                        acl_written_at=row[8] or 0.0,
                        score=0.0,
                    )
                    for row in await cur.fetchall()
                }
            # Preserve fused order and attach a descending score.
            return [
                by_id[cid].model_copy(update={"score": float(len(fused) - i)})
                for i, cid in enumerate(fused)
                if cid in by_id
            ]
        finally:
            await conn.close()


def _vec_literal(vec: list[float]) -> str:
    """pgvector accepts a bracketed string literal like [0.1,0.2,0.3]."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


class QdrantStore:
    """Scale tier. Dense search with the ACL payload pre-filter.

    Sparse fusion via Qdrant named sparse vectors is a follow-up; the authz bulk
    check remains the source of truth either way.
    """

    def __init__(
        self, url: str, api_key: str = "", collection: str = "pansoph", dim: int = 1024
    ) -> None:
        self._url = url
        self._api_key = api_key
        self._collection = collection
        self._dim = dim

    def _client(self) -> Any:
        from qdrant_client import AsyncQdrantClient

        return AsyncQdrantClient(url=self._url, api_key=self._api_key or None)

    async def upsert(self, hits: list[SearchHit], vectors: list[list[float]]) -> None:
        from qdrant_client import models

        client = self._client()
        try:
            await client.upsert(
                collection_name=self._collection,
                points=[
                    models.PointStruct(
                        id=hit.chunk_id,
                        vector=vec,
                        payload={
                            "doc_id": hit.doc_id,
                            "tenant_id": hit.tenant_id,
                            "allow_groups": hit.allow_groups,
                            "deny": hit.deny,
                            "acl_version": hit.acl_version,
                            "text": hit.text,
                            "consistency_token": hit.consistency_token,
                            "acl_written_at": hit.acl_written_at,
                        },
                    )
                    for hit, vec in zip(hits, vectors, strict=True)
                ],
            )
        finally:
            await client.close()

    async def hybrid_search(
        self, query_vector: list[float], query_text: str, flt: VectorFilter, limit: int
    ) -> list[SearchHit]:
        from qdrant_client import models

        # Mirror the Postgres pre-filter exactly: tenant match AND (the doc has no
        # groups OR its groups overlap the subject's). Expressed as a nested should
        # inside must, so a group mismatch is excluded, not merely deprioritized.
        # (A bare `should` in Qdrant is optional unless min_should is set, which is
        # why the previous tenant-must + group-should form leaked group docs.)
        group_clause: list[Any] = [
            models.IsEmptyCondition(is_empty=models.PayloadField(key="allow_groups"))
        ]
        if flt.any_groups:
            group_clause.append(
                models.FieldCondition(key="allow_groups", match=models.MatchAny(any=flt.any_groups))
            )
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="tenant_id", match=models.MatchValue(value=flt.tenant_id)
                ),
                models.Filter(should=group_clause),
            ]
        )
        client = self._client()
        try:
            result = await client.query_points(
                collection_name=self._collection,
                query=query_vector,
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
        finally:
            await client.close()
        hits: list[SearchHit] = []
        for point in result.points:
            payload = point.payload or {}
            hits.append(
                SearchHit(
                    chunk_id=str(point.id),
                    doc_id=payload.get("doc_id", ""),
                    tenant_id=payload.get("tenant_id", ""),
                    allow_groups=payload.get("allow_groups", []),
                    deny=payload.get("deny", []),
                    acl_version=payload.get("acl_version", 0),
                    text=payload.get("text"),
                    consistency_token=payload.get("consistency_token", ""),
                    acl_written_at=payload.get("acl_written_at", 0.0),
                    score=float(point.score or 0.0),
                )
            )
        return hits
