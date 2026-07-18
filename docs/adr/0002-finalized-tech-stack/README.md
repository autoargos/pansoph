# ADR-0002: pansoph finalized tech stack and ports-and-adapters architecture

**Status:** Accepted
**Date:** 2026-06-08
**Author:** Yash Karande

Supersedes the stack portions of [ADR-0001](../adr-0001-modular-knowledge-platform/). ADR-0001 still holds for the product vision, the five bets, the connector tiers, the access-control philosophy, and the Phase 1 scope. This ADR finalizes the concrete technology choices after a June 2026 market and library scan, and it locks the packaging shape.

## Context

ADR-0001 proposed a stack (Python and FastAPI, React with assistant-ui and shadcn, OpenRouter, Postgres, Qdrant, Redis, OpenFGA, MinIO, Docker Compose) but left several decisions explicitly open, and named some defaults that have since become risky. Before any code is written, the stack was pressure tested against current sources with these hard requirements in front:

1. **Scalability and long running tasks.** Ingestion and permission sync run continuously per connector instance, must be resumable after a crash, must respect source rate limits, and may fan out over thousands of documents.
2. **Many connectors.** The connector layer is the part that compounds, so it must be a real SDK with a low friction community contribution path.
3. **Self host first.** Trivial `docker compose up` for the full local demo, horizontal scale for a real org, no required cloud, no paywalled security.
4. **Total replaceability.** Authz, LLM, embeddings, reranker, parser, vector store, identity, connectors, and the UI are all ports with adapters. An operator can take only the pieces they want and replace or remove the rest, including the frontend.
5. **Bring your own model.** Every model touch point is provider agnostic. Local first (Ollama, vLLM, TEI), cloud optional (any OpenAI compatible endpoint, OpenRouter). No vendor is hardcoded.
6. **Secure by construction.** It holds an org's private knowledge plus many connectors' credentials, and it runs untrusted connector and MCP code.

## Method

Eight parallel research streams scanned 2025 and 2026 sources: backend language and runtime, task queue and durable execution, vector store and hybrid retrieval, authz and identity, RAG parsing and chunking, connector SDK and MCP bridge, frontend and chat UI, and deployment with datastores and security. Findings and the load bearing sources are captured in the References section. The three decisions that were genuinely the owner's to make were taken explicitly and are recorded below.

## Decisions taken

1. **Backend language: Python 3.12 or newer, FastAPI.** The language was genuinely reopened given the owner's Rust investment. It still lands on Python because nothing downstream forces a language (SpiceDB, the durable execution engine, Qdrant, and the SSE streaming protocol are all language neutral network contracts), while the decisive factor is community plugin extensibility through setuptools entry points plus the RAG, connector, and MCP ecosystem maturity. A Rust worker tier is reserved for a proven CPU bottleneck only, never preemptively.
2. **Default authz engine: SpiceDB**, with OpenFGA as a first class alternate adapter. SpiceDB ZedToken consistency directly closes the stale revocation new enemy problem, which is the single most security critical property of the headline feature. Its Watch API fits the connector ACL sync pipeline, and CheckBulk fits the overfetch and filter hot path. "Ground up" is reframed to mean pansoph owns the enforcement architecture, not the tuple engine.
3. **Repository shape: a uv managed monorepo of independently publishable packages.** Each port plus its adapters is a separately installable package with a clean public API. The platform is a thin assembly of those packages. Connectors, the browser extension, and the web app live in their own repositories.

## The number one principle: total replaceability

Every role is a port. Every default is one adapter behind that port. Nothing in the core depends on a concrete implementation. The list of ports in the first release:

`Connector`, `Parser`, `Embedder`, `Reranker`, `LLM`, `VectorStore`, `Authz`, `Identity`, plus the `IngestionWorkflow` engine port and the `ObjectStore` and `SecretStore` infrastructure ports.

The UI is replaceable too. pansoph is API first. `pansoph-core` exposes a stable REST API, an MCP server, and an SSE stream that follows the AI SDK UI Message Stream protocol. The shipped React app is the reference frontend and a consumer of those contracts, not a dependency of the core. An operator can remove it and build their own, or drive pansoph purely through the API and MCP.

Adapters are discovered as plugins through Python entry points with one namespaced group per port, following the pattern proven by LangChain and LlamaIndex integration packages. The core stays thin, the contracts stay minimal, and the core avoids per call overhead so that abstraction does not cost latency.

## Finalized stack by layer

| Layer | Final choice | Port | Notes |
|---|---|---|---|
| Backend language and API | Python 3.12+ with FastAPI | n/a | Async I/O bound workload, best plugin and AI ecosystem. Litestar is an acceptable alternative if FastAPI ergonomics fall short. |
| Durable execution and workers | DBOS for Phase 1 and local, Hatchet (MIT, Postgres) as the scale step up | `IngestionWorkflow` and `Step` | Postgres backed durable execution, resumable after crash, retries and backoff, cron and webhook triggers built in. No separate scheduler. Engine is hidden behind the port so connectors never call engine APIs. |
| Relational database | PostgreSQL 17 | n/a | Extensions pgvector, pgvectorscale, pgmq, pg_partman. Row level security with FORCE for pooled multi tenancy. Patroni plus etcd plus HAProxy for the org scale HA tier. |
| Vector store | pgvector plus pgvectorscale for local and small, Qdrant for the scale default | `VectorStore` | Qdrant is the only Apache 2.0 self host store that makes the ACL pre filter first class: tenant on disk locality, native multi valued keyword indexes for allow_groups, filterable HNSW, and ACORN to preserve recall under selective filters. pgvectorscale collapses metadata, vectors, and queue into one Postgres for the demo footprint. OpenSearch adapter optional for Lucene grade BM25F. |
| Hybrid retrieval | dense plus sparse plus reciprocal rank fusion (k equals 60) | part of `pansoph-retrieval` | Native dense and sparse named vectors in Qdrant, SQL RRF in the Postgres tier. Sparse via miniCOIL or plain BM25, not BM42. The enforcement path is overfetch 3 to 5 times, then SpiceDB bulk check, then keep the allowed subset, then rerank. |
| Parser | Docling default, MinerU or a VLM parser as escape hatch | `Parser` | One structured document model across PDF, DOCX, PPTX, XLSX, HTML, and images. MIT license. Section hierarchy feeds layout aware chunking. |
| Chunking and context | layout and section aware chunking plus Anthropic contextual retrieval | part of `pansoph-ingestion` | Prompt caching is mandatory in the implementation so contextual retrieval stays near one dollar per million document tokens. Semantic chunking is not the default. Late chunking is an option. |
| Embeddings | bge-m3 or Qwen3-Embedding, Matryoshka 768 or 1024, served via HF TEI | `Embedder` | bge-m3 emits dense, sparse, and multi vector from one model, which serves the hybrid requirement directly. Infinity when one server must host embedder plus reranker plus ColPali. Per collection dimension so swapping models is a per deployment setting. |
| Reranker | bge-reranker-v2-m3 or gte-reranker-modernbert-base, served via TEI | `Reranker` | Small cross encoders match larger models at a fraction of latency. Cohere, Jina, and Voyage as optional cloud adapters. Reranking is config gated. |
| LLM | model agnostic, any OpenAI compatible endpoint, OpenRouter, Ollama, vLLM | `LLM` | No hardcoded provider. A thin provider agnostic client, LiteLLM style. Local first by default, cloud opt in with a visible banner. |
| Authz and ReBAC | SpiceDB default, OpenFGA first class alternate, minimal AuthzPort | `Authz` | Minimal port surface: write_tuples, delete_tuples, bulk_check(subject, permission, object_ids, consistency_token) returning the allowed subset. Never use ListObjects or LookupResources on the hot path. Avoid exclusion operators in the model. pansoph owns the ACL ingestion to tuple mapping and the retrieval enforcement, which is the actual differentiator. A future Postgres only minimal engine is an escape hatch, not the default. |
| Identity | built in minimal user and group store, Zitadel as the recommended OIDC default | `Identity` | Zero dependency self host out of the box. External IdP over OIDC for teams: Zitadel recommended (single Go binary, SCIM, real multi tenancy), Keycloak for heavy enterprise, Authentik for homelab, Ory for headless. Map IdP groups to ReBAC group tuples so one nested group machinery serves both human auth and connector ACLs. |
| Connector SDK | declarative manifest first, Airbyte low code CDK lessons, Python escape hatch | `Connector` | The runtime implements pagination, retries, backoff, incremental cursors, and resumable sync once, on top of the durable execution engine, so every connector inherits them. Plugin discovery via entry points. One shared conformance suite gates every connector. Every source supports many independent instances, each with its own credentials, schedule, and ACL namespace. Shipped as a standalone package usable with zero pansoph. |
| Reach anything | public link fetcher plus MCP bridge | `Connector` adapters | The MCP bridge turns any MCP server into a connector by mapping resources and tools to documents. pansoph also exposes its own MCP server. This turns a very large catalog into instant connectors at near zero marginal cost. |
| Grabber | WXT browser extension, MV3, Readability capture | `Connector` adapter | Ingests only what the signed in user can already see, with consent. Never bypasses authentication, never solves anti bot challenges, never replays credentials, honors robots and rate limits. |
| Cache and broker | Valkey for cache and ephemeral pubsub, pgmq for durable queues | n/a | Valkey is BSD 3, Linux Foundation governed, a wire compatible drop in. This avoids both the Redis AGPL question and single vendor governance. |
| Object and blob store | Garage for demo and single host, SeaweedFS for org scale | `ObjectStore` (S3) | Explicitly not MinIO. The S3 API is the port so the two are interchangeable and a managed S3 is also valid. |
| Secrets | Postgres envelope encryption by default, OpenBao opt in | `SecretStore` | Connector OAuth tokens and service credentials are encrypted at rest with per tenant data keys and a root key from age or a KMS, so the default box needs no extra service. OpenBao (MPL 2.0, Linux Foundation) adds dynamic secrets, leasing, audit, and free namespaces. Not Vault, which is now BUSL. |
| Frontend | React 19 with Vite SPA, shadcn (Tailwind v4), assistant-ui plus Vercel AI Elements, Refine for admin | UI is a replaceable consumer of the API | Static assets served from Nginx, no SSR, no Node runtime. assistant-ui supplies the thread shell and the runtime abstraction, AI Elements supplies InlineCitation, Sources, Reasoning, and Response. Refine supplies the connector and access admin screens with its access control provider. |
| Streaming transport | SSE following the AI SDK UI Message Stream protocol | n/a | A wire contract, not an SDK lock in. Any backend emits text, reasoning, and source parts as SSE frames. This is why the backend language stayed a free choice. |
| Observability | OpenTelemetry SDKs everywhere, SigNoz as the default backend, off by default | n/a | OTel instrumentation keeps the backend swappable to a Grafana LGTM stack. |
| Packaging | single compose.yaml with profiles plus a Helm chart for scale | n/a | Profiles: core, workers, models, observability, secrets. Healthchecks and depends_on with realistic start_period on every service. Helm generated from Compose via Compose Bridge then hand tuned. |
| Security | gVisor or Firecracker microVM sandbox for untrusted connector and MCP code, egress allowlist proxy, RLS, SBOM and image signing | n/a | See the hardening checklist below. |
| Eval and CI | RAGAS plus DeepEval plus a custom NDCG and MRR golden set | n/a | Permission red team tests gate releases. |
| License | Apache 2.0 | n/a | A patent grant and a clean contrast to the paywalled enterprise directories of the incumbents. Some bundled dependencies are AGPL (Garage) or BSD (Valkey) or Apache (SpiceDB, Qdrant). Bundling does not taint pansoph's own Apache code, but it is documented for downstream redistributors. |

## Deviations from ADR-0001, called out

- **Redis becomes Valkey.** Redis relicensed to AGPLv3 plus single vendor governance. Valkey is the clean BSD 3 drop in.
- **MinIO is dropped for Garage and SeaweedFS.** MinIO stripped its Community Edition admin console in 2025, halted prebuilt images, entered maintenance mode in December 2025, and archived the repository in April 2026. It is a textbook rug pull and unsafe as a default.
- **Vault becomes OpenBao plus Postgres envelope encryption.** Vault is now an IBM product under BUSL 1.1. OpenBao is the OSI clean fork. The default needs no extra service at all.
- **OpenRouter as the model default becomes BYOM ports.** OpenRouter is one adapter behind the LLM and Embedder ports, not the default. Local first.
- **The open authz question is resolved.** SpiceDB default, OpenFGA alternate, both behind a minimal AuthzPort. The earlier framing of building our own Zanzibar is rejected as a multi quarter trap with no differentiation payoff.
- **Qdrant is confirmed and a Postgres vector tier is added.** pgvectorscale collapses the small and local footprint into Postgres, Qdrant is the scale default, both behind the VectorStore port.
- **A durable execution engine replaces a plain queue.** DBOS for Phase 1, Hatchet as the documented scale step up, behind an IngestionWorkflow port. A plain queue would force hand rolling checkpointing, cursor persistence, and replay, which is the durable execution wheel rebuilt worse.
- **Concrete RAG building blocks are named.** Docling, contextual retrieval with mandatory prompt caching, bge-m3 or Qwen3 embeddings, small rerankers, TEI serving, RAGAS and DeepEval. The core orchestration is built thin, not on LangChain or LlamaIndex as a spine.
- **Identity gains a concrete default.** Built in minimal store plus Zitadel as the recommended OIDC default.
- **A frontend pairing and an admin framework are named.** assistant-ui plus AI Elements plus Refine, on a Vite SPA.

## Building block packaging

A uv workspace monorepo. Each package is independently versioned and publishable to PyPI, with its own clean public API, and is usable standalone with no other pansoph package required.

- `pansoph-connector-sdk` The connector contract plus the declarative manifest runtime. Build connectors with zero pansoph.
- `pansoph-authz` The enforcement architecture, the AuthzPort, the SpiceDB and OpenFGA adapters, and the source ACL to tuple mappers. Usable as a standalone permission aware retrieval authorization library.
- `pansoph-retrieval` Hybrid retrieval, RRF, reranking, the overfetch and bulk check enforcement pipeline, and the VectorStore, Embedder, and Reranker ports.
- `pansoph-ingestion` Parse, chunk, contextualize, embed, and the IngestionWorkflow port over DBOS or Hatchet, plus the Parser port.
- `pansoph-mcp-bridge` MCP server to connector bridge, and pansoph's own MCP server surface.
- `pansoph-core` The thin assembly, the FastAPI app, and the REST plus MCP plus SSE surface.

Separate repositories: `pansoph-connectors` for community connector pull requests against one shared conformance suite, `pansoph-extension` for the WXT grabber, and `pansoph-web` for the React reference UI.

## Service and app names

The Python packages keep functional names so the codebase stays legible. The deployable and user facing units carry a short, cohesive, ownable brand under the scheme chosen on 2026-06-08.

- **agora** is the backend service: the REST API, the MCP server, and the SSE stream. It runs `pansoph-core`. Image `cyproto/pansoph-agora`.
- **scribe** is the ingestion and permission sync worker tier. It runs `pansoph-ingestion` on the durable execution engine. Image `cyproto/pansoph-scribe`.
- **lens** is the reference web UI, verification first. It lives in `apps/lens`. Image `cyproto/pansoph-lens`.
- **scout** is the browser grabber extension. It lives in `apps/scout`.
- **relay** is the MCP bridge, which turns any MCP server into a source. It is `pansoph-mcp-bridge`.

## Reference data flow

Ingestion per connector instance, as a durable workflow: list_changes since the stored cursor, then for each change fetch the raw document together with its source ACL, parse with Docling, chunk by section, generate contextual context with a cached prompt, embed, and upsert to the vector store with tenant id, instance id, allow_groups, and acl_version in the payload. In the same workflow, map the source ACL into authz tuples and write them through the AuthzPort, recording the consistency token. Deletions and permission changes propagate by webhook where the source supports it, otherwise by scheduled re sync, with revocations prioritized.

Retrieval and answering: embed the query, run hybrid dense plus sparse search with a coarse pre filter on tenant id and allow_groups, overfetch the top candidates 3 to 5 times over, run a single SpiceDB bulk check against the candidate ids at a freshness of at_least_as_fresh using the stored consistency token, keep the allowed subset, rerank, then answer with inline citations that deep link to the exact passage and that only open sources the user can already access.

## Local full Phase 1 demo

One command brings up the full vertical on one machine: `docker compose --profile core --profile workers --profile models up`. Services: Postgres 17 with pgvectorscale and pgmq, Valkey, Garage, SpiceDB, Ollama plus TEI, the FastAPI API, the DBOS workers, the React reference web app, and optionally Zitadel.

The demo exercises all of Phase 1 in breadth: connectors across all three tiers (GitHub, Google Drive, and Confluence as native, public link fetcher and an MCP server through the bridge as reach anything, and the browser extension as grabber), each multi instance, ingestion with source ACL import, ACL aware hybrid chat and search with inline citations, and the headline red team test live, where a second user provably cannot retrieve the first user's restricted document and a revocation is reflected after re sync. A single environment variable flips the LLM and embeddings to a cloud provider to prove BYOM.

Honest limits of running locally: breadth is complete but per connector depth is shallow (one repository or drive each), local models give modest answer quality, and the single node path does not exercise HA or the Helm tier. The passing ReBAC red team is what makes the demo meaningful rather than a scripted toy.

## Security hardening checklist

- Containers run rootless and non root, read only root filesystem, all capabilities dropped, per service CPU and memory limits.
- Untrusted connector and MCP code never runs in plain containers. It runs in gVisor for compute bound work or Firecracker or Kata microVMs for the strongest isolation, via a RuntimeClass.
- Egress is internal only by default, with a Squid style allowlist proxy so workers reach only provider APIs and configured sources. The cloud metadata IP is blocked.
- Per connector credential isolation with short lived scoped tokens and per tenant data keys. Read versus write tool scoping. Human in the loop gate for destructive actions.
- Tenant isolation enforced in both the authz engine namespace and the vector store payload, and with Postgres row level security FORCE.
- Supply chain: generate an SBOM with Syft, scan with Grype or Trivy, sign images with cosign, pin by digest, and rebuild and resign on patch.
- Secrets at rest use envelope encryption with the root key never in env or compose, and data keys rotate.
- Sanitize accessibility and document data before it reaches a model, never include secure fields or password fields.
- Structured immutable audit log of every connector action and admin operation.

## Testing and quality gates

Permission tests are first class and gate releases: red team retrieval that asserts a user can never retrieve another user's restricted document, revocation latency tests, and tenant and instance isolation tests. Every connector passes one shared conformance suite that exercises the contract, incremental sync, deletions, and ACL mapping. Retrieval quality is tracked with RAGAS and DeepEval on a domain golden set with custom NDCG and MRR, since public benchmark rankings do not predict a given corpus. The grabber tier has explicit guardrail tests that assert it never bypasses authentication, respects robots, and honors rate limits.

## Open items and pinning risks

- Default embedding model and dimension through the Embedder port still need a quality, cost, and storage decision per the ADR-0001 open question. Start with bge-m3 at 1024 dimensions.
- OpenFGA had a 2025 to 2026 BatchCheck performance regression and a BatchCheck correctness CVE, both fixed. Pin and load test the authz engine with a realistic tuple distribution before shipping.
- DBOS Conductor, the ops dashboard, is proprietary. The durability library is open source and sufficient, so observability must not depend on Conductor.
- OpenBao lacks native DR replication and FIPS builds as of April 2026. Fine for most, blocking for FIPS regulated orgs, where Vault Enterprise would be the swap.
- Ollama serializes requests and will feel slow under concurrency. The vLLM profile must be prominent so org deployers do not benchmark Ollama and conclude pansoph is slow.

## References

Backend language and runtime: Onyx system description (docs.onyx.app), Python free threading status (py-free-threading.github.io and the 3.14 howto), MCP Rust SDK rmcp and the Go SDK, Temporal Replay 2026 announcements, Python versus Go versus Rust for AI agents 2026 field guide (dev.to), Docling guide, Qdrant 50M raise (SiliconANGLE, March 2026).

Task queue and durable execution: Nango migration from Temporal to a Postgres orchestrator, Airbyte scaling with Temporal, Hatchet repo and durable tasks docs, DBOS transact py and the March 2026 features post and the Supabase DBOS post, Temporal Serverless Workers (May 2026), Postgres job queue benchmarking harness and pgmq, DBOS versus Temporal 2026 comparison, Onyx Celery and Redis usage.

Vector store and hybrid retrieval: Qdrant multitenancy docs and the 1.16 tiered multitenancy and ACORN post, Qdrant data privacy JWT RBAC article, Weaviate multi tenancy architecture, Milvus multi tenancy docs, pgvectorscale StreamingDiskANN, Oso right approach to authorization in RAG, Truto document level RBAC for RAG, ZeroEntropy open source rerank alternatives, HF TEI and Infinity, Qdrant BM42 and miniCOIL, Onyx enterprise RAG insights.

Authz and identity: PkgPulse OpenFGA versus Permify versus SpiceDB 2026, Auth0 OpenFGA improved ListObjects, OpenFGA running in production, Openlane 10s to 400ms BatchCheck case, AuthZed SpiceDB consistency and ZedTokens and performance docs, Oso authorization in RAG, TianPan permission aware retrieval (May 2026), Onyx connectors and auto sync permissions, Paragon permissions for production RAG, macwright Reading Zanzibar (May 2025) and zanzibar-pg, HouseOfFOSS Keycloak versus Authentik versus Zitadel 2026 and Zitadel SCIM docs, Cerbos best open source auth tools 2026.

RAG parsing and chunking: Anthropic contextual retrieval, OmniDocBench (CVPR 2025) and dots.ocr, Marker versus Docling versus MinerU 2026, Onyx repo and RAG docs, BentoML open source embedding models 2026 and Milvus choose an embedding model 2026, TEI versus Infinity versus FastEmbed comparison and Infinity repo, reranking cross encoders guide and AIMultiple reranker benchmark, BM25 to Corrective RAG benchmark (arXiv 2604.01733), Late Chunking (arXiv 2409.04701) and the 2026 chunking playbook, RAGAS TruLens DeepEval comparison (Atlan).

Connector SDK and MCP: Airbyte low code CDK overview and the maintaining hundreds of connectors post, Model Context Protocol overview and registry, Anthropic desktop extensions and MCP bundles, Nango declarative sync model, Onyx connectors and permission sync, WXT and Plasmo browser extension frameworks, hiQ and Van Buren and Meta v Bright Data scraping case law.

Frontend: AI SDK 5 announcement (Vercel), AI Elements repo and the InlineCitation component, assistant-ui repo, Glean deep linked citations docs, shadcn Tailwind v4 docs and changelog, Vite versus Next.js 2026, Refine and its access control docs, ShapeofAI citation patterns and Onyx self host search.

Deployment and security: Redis AGPLv3 announcement and InfoQ coverage, InfoQ MinIO maintenance mode and S3 alternatives and Blocks and Files MinIO console removal, self hosted S3 after MinIO (Garage, SeaweedFS, RustFS), Digitalis Vault versus OpenBao and the OpenBao site, pgvectorscale and pgmq and pg_partman, AWS Postgres RLS multi tenancy and Patroni, Red Hat vLLM or llama.cpp and HF TEI and Infinity, Northflank sandboxing AI agents 2026 and Docker sandbox security, SigNoz Grafana alternatives 2026, freeCodeCamp Docker Compose for production.
