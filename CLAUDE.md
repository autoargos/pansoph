# DOOR3 Company Memory fork of pansoph: agent guide

**Read this part first.** This repository is DOOR3's fork of the open source pansoph project
(upstream `autoargos/pansoph`). It is not where we develop the generic pansoph product. We use
pansoph as the secure, permission aware knowledge substrate for **DOOR3 Company Memory**. Part 2
below is the upstream engineering guide, kept word for word so upstream merges stay easy. It
remains the technical reference for how the code is built. Where Part 1 and Part 2 disagree,
Part 1 wins for work in this fork.

Writing convention (from upstream, still binding): no em dashes anywhere, in code, comments,
docs, or commit messages.

## 1. Mission

This repository exists to evaluate and operate pansoph as the secure, permission aware retrieval
foundation for DOOR3 Company Memory. Company Memory is an internal knowledge layer used by
employees and by internal AI agents: the IT Operations agent, the People Strategy Operations
agent, Engineering and Delivery agents, and others later. Sources: Slack, Jira, GitHub, Google
Drive, meeting transcripts, internal documents.

The one requirement everything serves: **a user or agent can only retrieve what the
authenticated user is authorized to see, and authorization happens before anything reaches an
LLM or a calling agent.**

Context docs: `docs/company-memory/README.md`, `docs/company-memory/SECURITY_AND_FIT_REVIEW.md`
(the audit, gap ids `CM-*`), and `docs/company-memory/UPSTREAM_STRATEGY.md`.

## 2. Priorities, in this order

1. Security.
2. Permission correctness.
3. Data isolation (users, groups, tenants, connector instances).
4. Citations and traceability.
5. Upstream compatibility.
6. Retrieval quality.
7. Performance.
8. Feature development.

When two conflict, the higher one wins. A faster or nicer feature that weakens 1 to 3 is
rejected.

## 3. Non goals (unless the user explicitly asks)

- Do not turn pansoph into the whole Company Memory product.
- Do not implement People, Project, Skills, or Decision graph models in pansoph core.
- Do not deeply fork the upstream architecture, rename the project, or rebrand it.
- Do not add agent specific workflows or prompts to the retrieval core.
- Do not add speculative features.
- Do not bypass or loosen authorization for convenience, demos, or to make a test pass.
- Do not ingest real DOOR3 data, deploy to cloud, or expose services publicly.
- Do not build scoring, ranking, or automated decisions about employees. People related output
  is evidence and context with citations.

## 4. Architecture boundary

```
Company sources (Slack, Jira, GitHub, Drive, transcripts, docs)
    -> pansoph (this repo): ingest, parse, index, ACL sync, identity, authz, retrieval,
       citations, REST and MCP
    -> Company Memory services (separate): MCP gateway, People, Project, Skills, Decisions,
       Experience memory, reporting, agents, orchestration
    -> IT Ops agent, People Strategy agent, Engineering agents, humans
```

pansoph provides ingestion, indexing, retrieval, authorization, ACL handling, citations, and
secure API and MCP access. Everything above that line lives outside pansoph core. It calls
pansoph **as the requesting user**, over REST or MCP, and receives only already authorized
results. A derived memory keeps the provenance and the ACL of its sources.

## 5. Security invariants (hard boundaries)

1. **No content without authorization.** Every path that returns chunk text, titles, ids,
   snippets, counts, or scores to a user, an LLM, or an agent goes through `retrieve()` and its
   `authz.bulk_check`. The vector pre filter is an optimization, never the decision.
2. **Authorization failure means deny.** An error, timeout, missing token, or unknown result
   returns nothing (or a 401 or 5xx), never a fall back identity or allow all.
3. **Identity comes only from a validated token.** Never from a request body, a tool argument
   the model can see, a default user, or a header a client can forge. Company Memory runs with
   `PANSOPH_REQUIRE_AUTH=true` and `PANSOPH_IDENTITY_ADAPTER=company_memory` (OIDC,
   verified email principal, configured users only).
4. **Tenant context is mandatory** and comes from the token, never from the body.
5. **MCP cannot bypass API authorization.** MCP tools call the same enforcement path as REST,
   with the same identity rules. There is no shared superuser MCP identity. An agent acts on
   behalf of a named user, or as a narrowly scoped service principal with its own explicit
   grants.
6. **No raw datastore access from agents.** Agents never receive Postgres, pgvector, Qdrant,
   SpiceDB, or object store credentials.
7. **Revocation must propagate.** Removed grants, removed group memberships, deleted source
   documents, deleted connector instances, and removed text must all disappear from both the
   authz engine and the vector store, and from derived artifacts.
8. **Evidence is kept.** Every factual answer retains its source ids (`doc_id`, `chunk_id`)
   where possible.
9. **Retrieved text is untrusted.** It may carry prompt injection. Never let retrieved content
   choose tools, change identity, or widen access.
10. **No secrets in code, logs, tests, or docs.** Use generated values in a gitignored `.env`.

Any change touching `packages/pansoph-retrieval`, `packages/pansoph-authz`,
`packages/pansoph-identity`, `pansoph_core/routes*.py`, `pansoph_core/mcp_server.py`, the
ingestion ACL path, or a connector's ACL mapping **must** include a permission test
(`@pytest.mark.permission`). Run the `permission-test` skill.

## 6. What is actually true today (verified 2026-09-29)

Do not trust Part 2 or the README over the code. See the status update at the top of the
review.

**Fixed in this fork:**

- CM-H1: stale group memberships are deleted from SpiceDB before the check.
- CM-H2 and CM-H3: empty and shrunk documents leave no stale chunks.
- CM-H5: deleting an instance purges its content.
- CM-H6: MCP auth is at the transport, and no tool takes a credential.

**Still true:**

- The upstream default still honors a body identity (CM-B1). It is closed only with
  `PANSOPH_REQUIRE_AUTH=true`. The Company Memory services refuse to start without it
  (`company_memory_mcp.guards`).
- The default `builtin` identity adapter authenticates nobody.
- The following are **not implemented**, despite Part 2 section 8 and ADR-0002:
  - RLS FORCE;
  - the connector and MCP sandbox;
  - network enforced egress;
  - an immutable audit store (the POC prints audit events to stdout).
- Connector syncs run inside the agora API process.
- Open permission gaps are pinned as strict xfail tests in
  `tests/permission/test_company_memory_known_gaps.py`. When you fix one, remove its marker
  and update the review. **Never weaken those assertions.**
- Connector readiness for Slack, GitHub, Jira, and Drive is PARTIAL or NOT READY.

**The demo stack:** `deploy/company-memory/`, run with the `company-memory` CLI in
`packages/company-memory-mcp` (see `docs/company-memory/DEMO.md`). Synthetic data only.
Never connect real DOOR3 sources to it. Its MCP server also accepts manual knowledge
(`add_company_memory_knowledge`): private by default, group scopes only under the publish
policy, indexed by the normal pipeline. Never add identity, tenant, or ACL arguments to it. Real DOOR3 identities may be
configured locally (`company-memory user add`, the gitignored `users.local.json`, tenant
`door3`); the knowledge stays synthetic until a security review approves a real-data pilot.
Never commit real emails, passwords, or tokens. Before a live demo, run `company-memory demo-reset`, log in, then
`company-memory demo-check` (read only; it must print DEMO READY).

## 7. Upstream strategy (summary of `docs/company-memory/UPSTREAM_STRATEGY.md`)

- Keep an `upstream` remote (`https://github.com/autoargos/pansoph.git`).
- Minimize edits to upstream files. Prefer, in order: configuration, then a new adapter behind
  an existing port (entry points), then a separate service, then an upstream PR, and a fork
  patch last.
- DOOR3 specific code goes in `docs/company-memory/`, `tests/permission/test_company_memory_*`,
  `.claude/skills/`, `packages/company-memory-mcp/`, `connectors/company_memory_synthetic/`,
  `deploy/company-memory/`, new `door3-*` or `company-memory-*` packages, or deploy overlays. Never
  scatter it through core modules.
- A fork patch to an upstream file must be small, opt in where possible, tested, in its own
  commit, and listed in the fork patch register in `UPSTREAM_STRATEGY.md`.
- Do not reformat, rename, or reorganize upstream files. Do not run formatters beyond the
  files you changed.
- Use the `upstream-sync-review` skill before merging `upstream/main`.

## 8. Process for this fork

- Conventional commits, no em dashes. Commit or push only when the user asks.
- Keep diffs small and atomic. Security fixes and dependency bumps go in separate changes.
- Before finishing: `uv run ruff check . && uv run ruff format --check . && uv run pytest -q`,
  plus `git diff --check`.
- Do not upgrade dependencies broadly. A targeted bump for a known advisory is fine in its own
  change.
- Do not ingest real company data in tests or fixtures. Use synthetic data only.

## 9. Skills for this fork (`.claude/skills/`)

| Skill | Use it when |
|---|---|
| `cm-security-review` | reviewing a change or component for permission boundaries, retrieval paths, MCP and API exposure, connector security |
| `permission-test` | writing or running authorization and leakage tests for a change |
| `connector-review` | evaluating a source connector's auth, scopes, ACL and identity mapping, and revocation |
| `company-memory-fit` | deciding whether a feature belongs in pansoph or in a Company Memory service |
| `upstream-sync-review` | reviewing divergence from upstream, or an incoming upstream merge |

---

# Part 2: upstream pansoph engineering guide (verbatim)

The text below is upstream's `CLAUDE.md`, unchanged. It is the reference for code structure,
ports, style, and testing. Read it with Part 1 section 6 in mind.

# pansoph engineering guide

This is the north star for everyone working on pansoph, human or AI. Read it before writing code. It exists so the project does not drift from its goal. If a change seems to fight this document, stop and reconsider the change, not the document.

Writing convention: no em dashes anywhere, in code, comments, docs, or commit messages. Use commas, periods, or parentheses.

---

## 1. What pansoph is, and the one goal

pansoph is an open source, self hostable, highly modular knowledge platform. It ingests an organization's knowledge from many disconnected sources into one access controlled retrieval, search, and chat layer.

The single thing that makes pansoph worth building is this: **document level access control, enforced at retrieval, built from the ground up, and also fully removable.** Independent guidance is consistent that the feature that decides adoption is permission enforcement, not retrieval accuracy and not latency. The incumbents paywall exactly that. pansoph gives it away under Apache 2.0 and makes it a swappable plugin.

The goal in one sentence: a user can only ever retrieve what they are already allowed to see, across every connected source, and an operator can run the whole thing on their own hardware with their own models and replace any part of it.

If you are ever unsure whether a change is worth making, ask whether it serves that sentence.

## 2. The one principle: everything is replaceable

Every role is a port. Every default is one adapter behind that port. Nothing in the core depends on a concrete implementation, including the UI. A user must be able to take only the pieces they want and replace or remove the rest.

Ports in the first release: `Connector`, `Parser`, `Embedder`, `Reranker`, `LLM`, `VectorStore`, `Authz`, `Identity`, `IngestionWorkflow`, `ObjectStore`, `SecretStore`.

The UI is replaceable too. pansoph is API first. The core (agora) exposes a stable REST API, an MCP server, and an SSE stream. The React app (lens) is the reference frontend and a consumer of those contracts, not a dependency.

Adapters are discovered as plugins through Python entry points, one namespaced group per port. A community contributor adds a connector or an embedder by publishing a package that registers an entry point, never by editing the core.

Consequences for how you write code:
- Do not import a concrete adapter from the core. Resolve it through the registry from configuration.
- Do not add a hard dependency from one building block package to another unless the dependency is part of that block's real contract. Shared shapes live in `pansoph-types`, which depends on nothing.
- Keep port surfaces minimal. A smaller port is easier to implement, so it stays replaceable.

## 3. Non negotiable rules

1. **Permission correctness is sacred.** The retrieval path overfetches candidates, then runs one authz bulk check, then keeps only the allowed subset, then reranks. The vector store pre filter is a speed optimization, never the source of truth. Never return a document to a user the authz port did not allow. Revocations must propagate, and a stale read must never leak a revoked document.
2. **Model agnostic, bring your own model.** Every model touch point goes through the LLM, Embedder, or Reranker port. No vendor is hardcoded in code or in docs. Local first by default (Ollama, vLLM, TEI), cloud opt in with a visible banner. "Tested with X" is not "requires X".
3. **Building blocks, not a monolith.** Each package is independently publishable and usable standalone. If you find yourself coupling two blocks, prove the coupling belongs in the contract or move the shared piece to `pansoph-types`.
4. **Security by construction.** pansoph holds an org's private knowledge and many connectors' credentials, and it runs untrusted connector and MCP code. See section 8.
5. **Never bypass authentication.** No anti bot, no CAPTCHA solving, no credential replay. The grabber captures only what the signed in user can already see, with consent, and honors robots and rate limits. This line is informed by current scraping case law and is not negotiable.
6. **No fiction.** Do not claim a capability works without an evidence trail (a passing test, a run log). A README that says a feature works when it does not is a bug.

## 4. Tech stack (finalized in ADR-0002)

The authoritative record is `docs/adr/0002-finalized-tech-stack/`. Summary:

| Layer | Choice |
|---|---|
| Language and API | Python 3.12+ with FastAPI. Rust worker tier only if a profile proves a CPU bottleneck. |
| Durable execution | DBOS (Postgres) for Phase 1 and local, Hatchet (MIT, Postgres) as the scale step up. Engine is the scheduler. |
| Relational DB | PostgreSQL 17 with pgvector, pgvectorscale, pgmq, pg_partman, RLS FORCE. |
| Vector store | pgvectorscale (local and small) and Qdrant (scale default). Hybrid dense plus sparse plus RRF (k=60). |
| Parser | Docling, with a MinerU or VLM escape hatch. |
| Chunking and context | layout and section aware, plus contextual retrieval with mandatory prompt caching. |
| Embeddings | bge-m3 or Qwen3, served via HF TEI. Matryoshka 768 or 1024. |
| Reranker | bge-reranker-v2-m3 or gte-reranker-modernbert, served via TEI. Config gated. |
| LLM | any OpenAI compatible endpoint, BYOM. |
| Authz | SpiceDB default, OpenFGA alternate, behind a minimal AuthzPort. |
| Identity | built in store, or Zitadel and other IdPs over OIDC. |
| Connectors | declarative manifest first (Airbyte low code CDK lessons), Python escape hatch. |
| Reach anything | public link fetcher plus the MCP bridge. |
| Grabber | WXT MV3 browser extension. |
| Cache and queue | Valkey (not Redis), pgmq for durable queues. |
| Object store | Garage or SeaweedFS (not MinIO), behind the S3 port. |
| Secrets | Postgres envelope encryption default, OpenBao opt in (not Vault). |
| Frontend | React 19 and Vite, shadcn, assistant-ui plus Vercel AI Elements, Refine admin, SSE UI Message Stream. |
| Observability | OpenTelemetry, SigNoz default, off by default. |
| Packaging | Docker Compose with profiles, Helm for scale. |
| License | Apache 2.0. |

Service and app names: **agora** (backend), **scribe** (ingestion workers), **lens** (web UI), **scout** (browser grabber), **relay** (MCP bridge).

Do not add a new heavy dependency (a new datastore, a new framework, a new broker) without an ADR. Small libraries that implement an adapter are fine.

## 5. Repo layout

```
packages/                  independently publishable Python packages
  pansoph-types/           shared shapes, zero pansoph deps
  pansoph-connector-sdk/   connector contract, declarative runtime, conformance suite, reference connector
  pansoph-authz/           enforcement architecture, AuthzPort, SpiceDB and OpenFGA adapters, ACL to tuple mappers
  pansoph-retrieval/       hybrid retrieval, RRF, rerank, overfetch and bulk check, vector and model ports
  pansoph-ingestion/       parse, chunk, contextualize, embed, IngestionWorkflow over DBOS
  pansoph-identity/        Identity port, built in store, OIDC adapter
  pansoph-infra/           ObjectStore and SecretStore ports and adapters
  pansoph-mcp-bridge/      MCP server to connector bridge, and pansoph's own MCP server
  pansoph-core/            the thin assembly: agora, the FastAPI REST plus MCP plus SSE surface
apps/
  lens/                    React reference UI
  scout/                   WXT grabber extension
connectors/                community connectors plus the native ones
deploy/                    Dockerfiles, compose helpers, Helm
docs/                      architecture map, pointers to the ADRs
tests/                     cross cutting tests, including the permission red team
```

## 6. Design patterns to follow

- **Ports and adapters.** A port is a `typing.Protocol`, runtime checkable, with the smallest surface that works. An adapter is a class that satisfies it and registers an entry point.
- **Registry resolution.** The core never imports adapters. It calls `pansoph_core.registry.load_adapter(group, name)` to resolve the configured adapter. A bad name fails fast with the list of available names.
- **Durable execution seam.** Connector code never calls the execution engine. It composes idempotent steps behind the `IngestionWorkflow` and `Step` ports. All side effects (HTTP fetches, embeds, upserts) live in steps so the orchestrator stays deterministic and replayable. Cursors are durable step outputs, so a crash resumes rather than restarts.
- **The enforcement path.** Embed query, hybrid search with a coarse tenant and group pre filter, overfetch 3 to 5 times, one `authz.bulk_check` at a freshness of at_least_as_fresh, keep the allowed subset, rerank. Never use a list all accessible call on the hot path. Never express deny as an authz exclusion tuple, apply it as a final filter.
- **Source ACL to tuples.** Connectors import source ACLs. The authz layer maps them to relationship tuples and a consistency token. Permissions usually expand, so the dangerous case is a contraction (a revocation), which is prioritized on re sync.
- **Declarative first connectors.** A simple REST source is a YAML manifest. The runtime owns pagination, retries, backoff, and incremental cursors so every connector inherits them. Only awkward sources drop to Python.
- **Contextual retrieval with caching.** Prepend a short generated context to each chunk before embedding. Prompt caching is mandatory, or the cost explodes.

### Connector implementation rule (mandatory)

Always implement a connector against the latest official API documentation of the source it connects to. Do not implement from memory. Before and while writing or changing a connector:

1. Consult the current official docs for that source (use the docs lookup tools or fetch the official site). Verify the exact endpoints, the authentication method and required scopes, the pagination model, the incremental sync mechanism (cursors, change feeds, webhooks), the rate limits, and the permission or ACL model.
2. Record the documentation links and the API version you targeted at the top of the connector module, with the date you verified them.
3. Map the source's real permission model to ACLs faithfully. Getting the ACL mapping wrong is a security bug, not a cosmetic one.
   - Credentials are RBAC scoped and vary. A configured instance may use a personal credential, an organization or app installation credential, a team or group scoped credential, or any combination. Do not assume a scope. Map ACLs to whatever the credential can actually reach, at the granularity the source exposes (user, team, group, org). Each instance has its own credential and its own ACL namespace, so different scopes never collide.
4. Re verify against the docs whenever the connector is changed, since these APIs move. If the docs and the code disagree, the docs win.

A connector built from stale assumptions is worse than no connector, because it fails silently or leaks. Treat the official docs as the source of truth.

## 7. Code quality standards

Python:
- Target 3.12+. Full type hints on every public function. `from __future__ import annotations` at the top of every module.
- `ruff check` and `ruff format` must pass. Line length 100. Imports grouped and sorted (ruff does this).
- `mypy` is the direction of travel. New code should type check; it becomes a hard gate as adapters land.
- Errors are values to handle, not to swallow. No bare `except`. Do not catch and ignore. Raise `NotImplementedError` with a pointer for genuine stubs, never a silent pass that pretends to work.
- Use `pydantic` models for data shapes and settings. Prefer `str` parameters, return owned values.
- No `print`. Use `logging` or the tracing setup. No commented out code, use git history.
- Keep files focused. If a module grows past roughly 500 lines, split it.
- Docstrings explain why, not what the code already says. One line for simple functions, a short paragraph for ports and tricky logic.
- Secrets never appear in code, logs, or committed files. The only secret material is in a gitignored `.env` or a secret store.

Frontend (lens):
- React 19, TypeScript strict, Vite. shadcn components. No secrets in the bundle. The UI only ever shows sources the user can open.

## 8. Security rules

- Untrusted connector and MCP code runs out of process in a sandbox (gVisor for compute bound, Firecracker or Kata microVMs for the strongest isolation), never inside agora.
- Egress is allowlisted. Workers reach only provider APIs and configured sources. The cloud metadata IP is blocked.
- Per connector credential isolation. Short lived scoped tokens, per tenant data keys, read versus write scoping, a human gate for destructive actions.
- Tenant isolation is enforced in both the authz namespace and the vector store payload, and with Postgres row level security FORCE.
- Sanitize document and accessibility data before it reaches a model. Never include password fields or secure fields.
- Supply chain: SBOM (Syft), scan (Grype or Trivy), sign images (cosign), pin by digest.
- Every connector action and admin operation is written to an immutable audit log.

## 9. Testing strategy

Tests are first class. The permission tests gate releases.

- **Permission red team is the headline suite.** It asserts a user can never retrieve another user's restricted document, that revocations propagate, and that tenants and instances are isolated. Marked `@pytest.mark.permission` and run as a standalone CI gate. A green checkmark there means permissions are enforced.
- **Connector conformance.** Every connector, native or declarative, passes the one shared conformance suite in `pansoph-connector-sdk` (`assert_conforms`). It exercises the contract, incremental sync, deletions, and ACL mapping. A connector that does not pass does not merge.
- **Retrieval quality.** RAGAS plus DeepEval on a domain golden set with custom NDCG and MRR. Public benchmark rankings do not predict a given corpus, so measure on yours.
- **Unit tests are real.** No `assert a == a`. Test boundaries, edge cases, error paths, and invariants. Property based tests (hypothesis) where the input space is large. Async tests with `pytest-asyncio` (auto mode).
- **Integration tests** that need live services are marked `@pytest.mark.integration` and run separately.
- **Regression tests** for any bug found against a real model or a real source, named so the bug cannot return silently.

Every change to the retrieval enforcement path or an authz adapter must come with a permission test. Every new connector comes with a conformance run.

## 10. What pansoph explicitly does not do

- No authentication bypass, no anti bot or CAPTCHA solving, no credential replay scraping.
- No hosted multi tenant SaaS in Phase 1. Self host open source only.
- No SCIM or SAML in Phase 1. OIDC is enough to start.
- No GraphRAG or RAPTOR enabled by default. The indexer port is reserved for them.
- No sub document field level redaction in Phase 1. The document is the unit of access control.
- Do not reinvent Zanzibar. We own the enforcement architecture and embed a proven tuple engine.

## 11. Process

- Conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `perf:`, `chore:`). No em dashes in the message. No co-author trailer.
- Keep diffs small and atomic, one logical change at a time.
- Run `uv run ruff check . && uv run ruff format --check . && uv run pytest -q` before committing. Keep the tree green.
- Adding a connector: first read the source's latest official API docs (see the connector implementation rule in section 6), then implement the contract or write a manifest, register the `pansoph.connectors` entry point, pass `assert_conforms`, map the source ACL faithfully, and record the doc links and verified date in the module.
- Adding an adapter for any port: implement the protocol, register the port's entry point group, keep it standalone.
- Adding a dependency that is a new datastore, framework, or broker: write an ADR first.

## 12. Pointers

- Vision: `docs/adr/0001-modular-knowledge-platform/`
- Finalized stack and ports: `docs/adr/0002-finalized-tech-stack/`
- Architecture map: `docs/ARCHITECTURE.md`

When in doubt, return to section 1. A user can only ever retrieve what they are already allowed to see, across every connected source, on their own hardware, with their own models, with every part replaceable. Everything else serves that.
