"""The ACL aware retrieval pipeline: overfetch, bulk check, rerank.

This ties the ports together. It is the enforcement path described in ADR-0002.
The bulk check is the source of truth; the vector pre filter only shrinks the
candidate set. `deny` entries are applied as a final filter here, never as authz
exclusion tuples.
"""

from __future__ import annotations

from pansoph_authz import AuthzPort, Principal

from .ports import Embedder, Reranker, SearchHit, VectorFilter, VectorStore

OVERFETCH = 4  # overfetch the top candidates this many times over


async def retrieve(
    *,
    query: str,
    subject: Principal,
    tenant_id: str,
    subject_groups: list[str],
    final_k: int,
    vector_store: VectorStore,
    embedder: Embedder,
    authz: AuthzPort,
    reranker: Reranker | None = None,
) -> list[SearchHit]:
    """Return permission correct, reranked hits for a query.

    Steps: embed, hybrid search with coarse pre filter, overfetch, bulk authz
    check to the allowed subset, then optional rerank to final_k.
    """
    query_vector = (await embedder.embed([query]))[0]

    flt = VectorFilter(tenant_id=tenant_id, any_groups=subject_groups)
    candidates = await vector_store.hybrid_search(
        query_vector=query_vector,
        query_text=query,
        flt=flt,
        limit=final_k * OVERFETCH,
    )

    if not candidates:
        return []

    allowed_ids = await authz.bulk_check(
        subject=subject,
        permission="read",
        object_ids=[hit.doc_id for hit in candidates],
    )
    # The authz bulk check is the source of truth for grants. `deny` is then applied
    # as a final filter (ADR-0002): a chunk denied to the subject directly or to any
    # of their groups is removed even if a group grant let it pass the check.
    denied_principals = {subject.id, *subject_groups}
    allowed = [
        hit
        for hit in candidates
        if hit.doc_id in allowed_ids and denied_principals.isdisjoint(hit.deny)
    ]

    if reranker is not None and allowed:
        return await reranker.rerank(query, allowed, top_n=final_k)
    return allowed[:final_k]
