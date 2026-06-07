# pansoph-ingestion

The continuous, per instance, durable ingestion pipeline, plus its ports.

## The workflow, per connector instance

A durable workflow so a crash at document 8,000 of 10,000 resumes rather than restarts:

1. list_changes since the stored cursor (cursor is a durable step output).
2. For each change, fetch the raw document together with its source ACL.
3. Parse with the Parser port (Docling default).
4. Chunk by section.
5. Generate contextual context per chunk with a cached LLM prompt (contextual retrieval).
6. Embed with the Embedder port.
7. Upsert to the VectorStore port with tenant id, instance id, allow_groups, and acl_version.
8. Map the source ACL to authz tuples and write them through the AuthzPort, recording the consistency token.
9. Handle deletions and permission changes, revocations first.

The engine is hidden behind the `IngestionWorkflow` and `Step` ports. DBOS backs Phase 1 and the local demo (a library, no extra infrastructure, uses the Postgres pansoph already needs). Hatchet is the documented scale step up. Connector code never calls engine APIs.

## Ports

- `Parser`: bytes to a structured document. Default Docling, with a MinerU or VLM escape hatch.
- `LLM`: model agnostic, for contextual context generation and answering. BYOM.
- `IngestionWorkflow` and `Step`: the durable execution seam.

Contextual retrieval must use prompt caching, or its cost explodes. With caching and a small model it stays near one dollar per million document tokens.
