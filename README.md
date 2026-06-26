# pansoph

> A self hostable, highly modular knowledge platform with ground up, removable, document level access control.

pansoph (from pansophy, universal knowledge, always lower case) ingests an organization's knowledge from many disconnected sources into one access controlled retrieval, search, and chat layer. Its headline feature is relationship based access control enforced at retrieval, built from the ground up and also fully removable, so an operator can plug in their own identity and authorization stack or switch it off entirely.

Apache 2.0 licensed. Self host first. No required cloud. No paywalled security. Bring your own model.

## The one principle: everything is replaceable

Every role is a port. Every default is one adapter behind that port. Nothing in the core depends on a concrete implementation. You can take only the pieces you want and replace or remove the rest, including the UI.

Ports in the first release: `Connector`, `Parser`, `Embedder`, `Reranker`, `LLM`, `VectorStore`, `Authz`, `Identity`, `IngestionWorkflow`, `ObjectStore`, `SecretStore`. The frontend is a replaceable consumer of the REST, MCP, and SSE contracts, not a dependency of the core.

## Monorepo layout

This is a uv workspace. Each package under `packages/` is independently versioned and publishable to PyPI, with a clean public API, and is usable standalone with no other pansoph package required.

```
pansoph/
  packages/
    pansoph-connector-sdk/   the connector contract + declarative manifest runtime
    pansoph-authz/           enforcement architecture + AuthzPort + SpiceDB/OpenFGA adapters + ACL->tuple mappers
    pansoph-retrieval/       hybrid retrieval + RRF + rerank + overfetch/bulk-check + VectorStore/Embedder/Reranker ports
    pansoph-ingestion/       parse/chunk/contextualize/embed + IngestionWorkflow + Parser/LLM ports
    pansoph-mcp-bridge/      MCP server -> connector bridge, and pansoph's own MCP server
    pansoph-core/            thin assembly: FastAPI app + REST + MCP + SSE surface
  apps/
    lens/                    "lens", the React 19 + Vite reference UI (will be split to its own repo)
    scout/                   "scout", the WXT MV3 browser grabber (will be split to its own repo)
  connectors/                community connectors (will be split to its own repo: pansoph-connectors)
  deploy/                    Helm chart and ops
  docs/                      architecture, pointers to the ADRs
  compose.yaml               the self host stack, profiles: core, workers, models, observability, secrets
```

## The named services and apps

Deployable and user facing units carry a short, cohesive brand. The Python packages keep their functional names.

| Brand | What it is | Runs / lives in |
|---|---|---|
| **agora** | the backend: REST, MCP server, and SSE stream. The public square where you ask. | `pansoph-core`, image `cyproto/pansoph-agora` |
| **scribe** | the ingestion and permission sync workers. Gathers and transcribes every source. | `pansoph-ingestion`, image `cyproto/pansoph-scribe` |
| **lens** | the reference web UI. Verification first: see through every answer to its source. | `apps/lens`, image `cyproto/pansoph-lens` |
| **scout** | the browser grabber extension. Brings back only what you can already see, with consent. | `apps/scout` |
| **relay** | the MCP bridge. Any MCP server becomes a source. | `pansoph-mcp-bridge` |

## The stack, in one line each

- Backend: Python 3.12+ and FastAPI. Durable ingestion on DBOS (Postgres), Hatchet as the scale step up.
- Data: PostgreSQL 17 with pgvector, pgvectorscale, pgmq, pg_partman. Qdrant as the scale vector default.
- Authz: SpiceDB default, OpenFGA alternate, behind a minimal AuthzPort. Identity: built in store or Zitadel over OIDC.
- Models, BYOM: Ollama and vLLM local, any OpenAI compatible endpoint or OpenRouter cloud. Served embeddings and rerankers via HF TEI.
- Retrieval: Docling parsing, contextual retrieval, bge-m3 or Qwen3 embeddings, small cross encoder reranker, hybrid plus RRF, overfetch then bulk authz check.
- Infra: Valkey (not Redis), Garage or SeaweedFS (not MinIO), Postgres envelope encryption or OpenBao (not Vault).
- Frontend: React 19 and Vite, shadcn, assistant-ui plus Vercel AI Elements, Refine admin, SSE UI Message Stream.

See the decision records for the full rationale and the June 2026 sources:

- ADR-0001 the product vision: [`docs/adr/0001-modular-knowledge-platform/`](docs/adr/0001-modular-knowledge-platform/README.md)
- ADR-0002 the finalized tech stack: [`docs/adr/0002-finalized-tech-stack/`](docs/adr/0002-finalized-tech-stack/README.md)

## Quick start

```bash
# install the workspace toolchain
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync

# bring up the full local demo (Postgres, Valkey, Garage, SpiceDB, Ollama, TEI, API, workers, web)
docker compose --profile core,workers,models up
```

> Heads up: this profile brings up roughly a dozen services (Postgres, Valkey, an object
> store, SpiceDB, an identity provider, model servers, the API, and workers). It is a
> full self-hosted stack, not a five-minute taste. A single-command, single-connector
> demo behind a much smaller footprint is on the roadmap. Until then, see `DEMO.md` for
> the guided walkthrough.

## Status

Pre-1.0 and under active development. This section tracks real, verified capability
honestly (see the "No fiction" rule in `CLAUDE.md`).

### What works today

These work end to end against in-memory fakes, with the logic covered by the test suite
(133 tests pass) and the permission red-team (27 tests) gating CI:

- The permission-enforcement retrieval path: embed, hybrid search with overfetch, a
  single `authz.bulk_check` at a freshness tied to the last permission write, keep only
  the allowed subset, apply deny as a final filter, then optionally rerank
  (`packages/pansoph-retrieval/src/pansoph_retrieval/pipeline.py`).
- The agora REST, SSE, and discovery surface: `/api/search`, `/api/chat` (SSE),
  `/api/grab`, `/api/health`, and connector-instance management
  (`packages/pansoph-core/src/pansoph_core/routes.py`).
- Authz adapters for SpiceDB (gRPC, bulk check, consistency token), OpenFGA (batch
  check), and a no-op adapter, behind a minimal `AuthzPort`.
- Vector adapters for pgvector (dense plus sparse plus RRF) and Qdrant.
- Fifteen connectors and the connector SDK contract, with a hermetic, offline
  conformance harness.

### What is in progress (not yet verified)

We say this plainly so you can trust the line above:

- **Live-engine integration is unverified.** The permission suite runs on in-memory
  fakes today. It validates the enforcement logic, not a live SpiceDB, OpenFGA, or
  pgvector engine. Integration tests against real engines are the next priority, because
  a schema or freshness bug could otherwise ship with green CI.
- **Durable ingestion is a placeholder.** The ingestion worker is a stub and scheduling
  is a best-effort in-process loop. The durable execution engine is not wired yet.
- **Connectors are full-sync with content-fidelity caveats.** Most connectors currently
  sync fully rather than incrementally, content extraction is demo-grade for several
  sources, and ACL mapping fidelity varies by source. Treat the connectors as functional
  but shallow until each one ships a conformance fixture and a verified ACL mapping.

The permission core is real and tested in logic. The live proof and the connector depth
are the work in flight. Do not claim a feature works without an evidence trail.
