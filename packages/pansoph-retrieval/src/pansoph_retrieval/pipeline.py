"""The ACL aware retrieval pipeline: overfetch, bulk check, rerank.

This ties the ports together. It is the enforcement path described in ADR-0002.
The bulk check is the source of truth; the vector pre filter only shrinks the
candidate set. `deny` entries are applied as a final filter here, never as authz
exclusion tuples.
"""

from __future__ import annotations

from pansoph_authz import AuthzPort, ConsistencyToken, Principal

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
    at_least_as_fresh: ConsistencyToken | None = None,
) -> list[SearchHit]:
    """Return permission correct, reranked hits for a query.

    Steps: embed, hybrid search with coarse pre filter, overfetch, bulk authz
    check to the allowed subset, then optional rerank to final_k. `at_least_as_fresh`
    overrides the candidate derived consistency token when the caller holds a newer one.
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

    # Thread the freshest stored consistency token so the bulk check reads no staler
    # than the most recent ACL write among the candidates. Falls back to None (the
    # adapter's own freshness) for chunks ingested before tokens were stored.
    freshest = max(candidates, key=lambda h: h.acl_written_at, default=None)
    token = (
        ConsistencyToken(freshest.consistency_token)
        if freshest and freshest.consistency_token
        else None
    )
    # A caller that just wrote this subject's memberships (adding or revoking) passes that
    # write's token, which is newer than every candidate's ACL write, so the check reads
    # neither a stale revoked membership nor misses a just added one.
    if at_least_as_fresh:
        token = at_least_as_fresh
    allowed_ids = await authz.bulk_check(
        subject=subject,
        permission="read",
        object_ids=[hit.doc_id for hit in candidates],
        at_least_as_fresh=token,
    )
    # The authz bulk check is the source of truth for grants. `deny` is then applied
    # as a final filter (ADR-0002): a chunk denied to the subject directly or to any
    # of their groups is removed even if a group grant let it pass the check.
    denied_principals = {subject.id, *subject_groups}
    allowed = [
        hit
        for hit in candidates
        if hit.doc_id in allowed_ids
        # Defense in depth: never cross the tenant boundary even if the vector
        # pre-filter over-returned. The authz check already enforces tenant isolation
        # (public grants to the tenant everyone group, group grants are tenant
        # namespaced), so this redundant guard on each candidate's stored tenant means
        # a single pre-filter bug cannot leak a cross-tenant document.
        and hit.tenant_id == tenant_id
        and denied_principals.isdisjoint(hit.deny)
    ]

    if reranker is not None and allowed:
        return await reranker.rerank(query, allowed, top_n=final_k)
    return allowed[:final_k]
