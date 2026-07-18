# pansoph architecture

This is the working map. The authoritative decisions live in the ADRs:

- [ADR-0001](adr/0001-modular-knowledge-platform/README.md) the product vision.
- [ADR-0002](adr/0002-finalized-tech-stack/README.md) the finalized tech stack and the ports.

## The one principle: everything is replaceable

Every role is a port. Every default is one adapter. Nothing in the core depends on a concrete implementation, including the UI. Adapters are discovered as plugins through Python entry points, one namespaced group per port.

| Port | Entry point group | Default adapter | Package |
|---|---|---|---|
| Connector | `pansoph.connectors` | native + manifest + mcp | `pansoph-connector-sdk`, `pansoph-mcp-bridge` |
| Parser | `pansoph.parsers` | docling | `pansoph-ingestion` |
| Embedder | `pansoph.embedders` | tei (bge-m3) | `pansoph-retrieval` |
| Reranker | `pansoph.rerankers` | tei (bge-reranker-v2-m3) | `pansoph-retrieval` |
| LLM | `pansoph.llms` | openai_compatible (BYOM) | `pansoph-ingestion` |
| VectorStore | `pansoph.vectorstores` | pgvectorscale (local), qdrant (scale) | `pansoph-retrieval` |
| Authz | `pansoph.authz` | spicedb (openfga alternate) | `pansoph-authz` |
| Identity | `pansoph.identity` | builtin (zitadel oidc) | `pansoph-identity` (planned) |
| ObjectStore | `pansoph.objectstores` | s3 (garage/seaweedfs) | `pansoph-infra` (planned) |
| SecretStore | `pansoph.secretstores` | envelope (openbao) | `pansoph-infra` (planned) |
| IngestionWorkflow | n/a (engine seam) | dbos (hatchet scale) | `pansoph-ingestion` |

## The named units

- **agora** the backend: REST, MCP server, SSE stream. Runs `pansoph-core`.
- **scribe** the ingestion and permission sync workers. Runs `pansoph-ingestion`.
- **lens** the reference web UI. `apps/lens`. Replaceable: it only consumes agora's REST, MCP, and SSE contracts.
- **scout** the browser grabber extension. `apps/scout`.
- **relay** the MCP bridge. `pansoph-mcp-bridge`.

## The two hot paths

Ingestion (durable, per connector instance): list_changes since cursor, fetch doc + ACL, parse, section chunk, contextualize with a cached prompt, embed, upsert to the vector store with tenant and group metadata and an ACL version, and write authz tuples with a consistency token. Deletions and revocations propagate, revocations first.

Retrieval (permission correct): embed query, hybrid dense plus sparse search with a coarse tenant and group pre filter fused by RRF, overfetch 3 to 5 times, one authz bulk check at a freshness of at_least_as_fresh, keep the allowed subset, rerank, answer with deep linked citations the user can actually open.

## Build vs adopt

Adopt behind ports: Docling, TEI, bge-m3, rerankers, SpiceDB, OpenFGA, Qdrant, pgvectorscale, DBOS, Hatchet, Zitadel. Build (the value): the thin orchestration, the ports, the section aware chunker, the contextual context generator, hybrid plus RRF, the ACL ingestion to tuple mapping, the overfetch plus bulk check enforcement, and the deep linked citation contract.
