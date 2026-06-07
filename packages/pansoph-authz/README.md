# pansoph-authz

Document level access control enforced at retrieval. This is pansoph's headline feature, and it is a standalone library: you can use it for permission aware retrieval in any RAG system, not just pansoph.

## What pansoph owns, and what it does not

pansoph does not reinvent Zanzibar. The tuple engine is an adapter. SpiceDB is the default, OpenFGA is a first class alternate, and the port is minimal enough that a Postgres only engine could implement it later. What pansoph owns, and what is the actual differentiator, is the enforcement architecture:

1. The ACL ingestion mapping: turn a source ACL (Confluence restrictions, Drive permissions, GitHub teams) into authz tuples, and create, update, and delete them as source permissions change. See `mappers.py`.
2. The retrieval enforcement path: overfetch candidates from the vector store, run one bulk check, keep the allowed subset, then rerank. See `pansoph-retrieval`, which calls this port.
3. The consistency plumbing: each document carries an ACL version and a consistency token, so a revocation is never silently stale.

## The minimal port

```python
class AuthzPort(Protocol):
    async def write_tuples(self, tuples: list[Tuple]) -> ConsistencyToken: ...
    async def delete_tuples(self, tuples: list[Tuple]) -> ConsistencyToken: ...
    async def bulk_check(
        self,
        subject: Principal,
        permission: str,
        object_ids: list[str],
        at_least_as_fresh: ConsistencyToken | None = None,
    ) -> set[str]:  # the allowed subset of object_ids
        ...
```

Never use ListObjects or LookupResources on the hot path. Overfetch from the vector store, then bulk check the candidates. Avoid exclusion operators in the model, they wreck bulk check latency.

## Why SpiceDB is the default

ZedToken consistency closes the stale revocation new enemy problem, the Watch API fits the connector ACL sync pipeline, and CheckBulk fits the overfetch and filter hot path. OpenFGA is the lower ops alternate for single instance deployers. See ADR-0002 for the full comparison and the June 2026 sources.
