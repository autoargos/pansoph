# Permission Layer Repositioning Plan (Workstream C)

> **Status of this document:** PROPOSAL. It describes work that WILL be done. It does not claim any capability already works. Where it references current code, it cites a real file path. Where it describes a future state, it says "proposed" or uses a checkbox. The "No fiction" rule from `CLAUDE.md` applies to this plan as much as to the README.
>
> **Author:** Yash Karande. **Date:** 2026-06-23 (sources re-verified 2026-06-25).
>
> **Working directory for all commands:** `/Users/yash/work/autoargos/pansoph`. Run `export PATH="$HOME/.local/bin:$PATH"` first so `uv` is found. No em dashes in any file, code, comment, or commit message. Apache-2.0, self-host-first, model-agnostic.

---

## 1. Goal in one sentence

Stop positioning pansoph as "another self-hosted Glean/Onyx", and reposition it as **the removable, engine-agnostic permission layer for RAG**: a primitive that bolts onto any vector store and any ReBAC engine, proven by ONE single-connector demo that shows Alice cannot retrieve Bob's document in under 5 minutes from a single command.

---

## 2. The strategic case

### 2.1 The platform axis is unwinnable. Do not fight there.

The "self-hosted knowledge platform" slot is occupied and saturating at the top:

- **Onyx** (ex-Danswer): ~30,519 stars, ~$10M seed (Khosla, First Round), customers Netflix / Ramp / Thales, 50+ connectors, latest release v4.2.0 (2026-06-22). Its README leads with `curl -fsSL https://onyx.app/install_onyx.sh | bash`. It already does ACL-sync and permission-aware retrieval. We are not going to out-execute that on platform breadth as a solo project.
- **Glean**: $7.2B valuation, ~$300M ARR (May 2026, 3x growth in 15 months). The commercial ceiling is not the contested ground.
- **RAGFlow**: ~83,540 stars, Apache-2.0. The OSS RAG-engine top is full.
- **PipesHub**: ~3.0k stars, Apache-2.0, "open source Glean alternative", already shipping permission-aware search with source-ACL inheritance across 30+ connectors.
- **Cognita** (TrueFoundry, VC-backed, modular RAG): **ARCHIVED 2026-03-13** at ~4,408 stars, banner reads "This project is no longer actively maintained." This is the direct precedent: the "modular RAG platform" thesis underperforms even with VC funding and a team. pansoph is a solo project. The platform framing is the trap that killed the closest analog.

Conclusion: competing on platform completeness loses. The README's current headline ("a self hostable, highly modular knowledge platform", `README.md:3`) is competing exactly where the field is most crowded and where a solo developer is weakest.

### 2.2 The wedge: document-level access control is paywalled by the OSS leader.

Onyx's own docs gate document-level RBAC, ACL-mirroring, and the Curator role to the **paid Enterprise Edition**. Verbatim from `docs.onyx.app` (fetched 2026-06-25):

- "RBAC protection to documents and resources are only available in the Enterprise Edition of Onyx"
- "Different access to documents is only available in the Enterprise Edition of Onyx"
- "Curator: Document management and content curation (only available on Enterprise Edition)"
- The GitHub connector's permission sync is "available only on Cloud and the Enterprise Edition of Onyx."

The MIT Community Edition lacks document-level access control. **That is the wedge.** pansoph's headline claim, "no paywalled security" (`README.md:7`), is a real, current, verifiable differentiator against the OSS category leader. We should lead with it, not bury it as feature number three.

### 2.3 But the wedge is narrow, and the primitive can be commoditized. Be honest about this.

The primitive-competitor space is real and well funded:

- **SpiceDB / AuthZed** and **OpenFGA / Auth0 FGA** already document the canonical RAG-authz flow (overfetch candidates, then `CheckBulkPermissions` / `BatchCheck` post-filter, or `LookupResources` / `ListObjects` pre-filter). This is the *exact* pattern pansoph implements in `packages/pansoph-retrieval/src/pansoph_retrieval/pipeline.py:18-75`. We did not invent it. We are shipping the industry-consensus pattern.
- **Cerbos** ships authz-for-RAG via its `PlanResources` Query-Plan API, returning an AST translated into native vector-store filters (Pinecone, Weaviate, Chroma, Qdrant, FAISS), enforcing **before** retrieval.

So why is there room at all? Because every one of those is delivered as **vendor SDK glue**, not as a packaged, removable, Apache-2.0, **engine-agnostic** OSS enforcement library:

- SpiceDB/OpenFGA give you a *client* and a *pattern doc*, not a drop-in retrieval enforcement library you can `pip install` and point at your existing vector store.
- Cerbos's mechanism is commercial-vendor adapters and the pre-filter *is* the gate (a different, also-valid architecture). It is not a removable OSS library.
- The closest OSS analog, **Ory `rerag-rebac`** (Zanzibar-style ReBAC for RAG via Ory Keto + SQLite-vec), is a **reference/demo repo** (34 stars, no SPDX license set, last pushed 2025-11-13), not a reusable multi-engine library.

The empty cell in the matrix is: *removable* AND *Apache-2.0* AND *engine-agnostic over the vector store* AND *engine-agnostic over the ReBAC backend* AND *packaged as a library*. pansoph already has the code for that cell. The repositioning is to **name it, extract it, and ship it as the product**, not as a sub-feature of a platform.

**Honest caveats, stated up front:**

- This is a thin wedge. A determined SpiceDB or OpenFGA could publish a first-party `*-rag` library and absorb most of the differentiation in a quarter. The defensibility is execution speed, engine-agnosticism (we span both SpiceDB and OpenFGA behind one port), and OSS trust, not a patent.
- The primitive is commoditizable. Our durable edge has to come from being the *neutral* layer that even competitors (Onyx CE users, RAGFlow users, a homegrown LangChain stack) adopt because it is the only one not tied to a single authz vendor or a single vector store.
- The current proof is **insufficient**. The enforcement logic is validated only on in-memory fakes (`tests/permission/test_redteam.py`, 27 tests on `FakeAuthz`/`FakeVectorStore`). `pytest -m integration` collects zero tests. A SpiceDB schema bug, a ZedToken staleness bug, or a pgvector pre-filter group-leak would ship CI-green today. **We cannot credibly sell a security primitive without live integration tests.** That gap is owned by Workstream B (live integration testing) and is a hard dependency for the public claims in this plan. This plan must not assert "proven against a live engine" until Workstream B lands.

### 2.4 The regulatory tailwind (durable, but framed correctly).

On **2 Aug 2026** the EU AI Act activates the Commission's enforcement and penalty powers over GPAI providers under Article 101 (fines up to 3% of worldwide turnover or EUR15M, whichever is higher). Free-and-open-licence GPAI providers get reduced obligations (exemption from the Article 53(1)(a)(b) technical-documentation/downstream-info duties and the Article 54(6) EU-representative duty), which favors Apache-2.0 / self-host / BYO-model tooling.

Frame this correctly. The high-risk Annex III obligations are **deferred to 2 Dec 2027** via the Digital Omnibus (provisional agreement 7 May 2026, Parliament endorsed 16 June 2026, Council adoption/OJ publication expected July 2026, not yet fully formally adopted as of 2026-06-25). So the durable tailwind for pansoph is **data sovereignty and self-hosting**, not a specific high-risk compliance deadline we can market a countdown to. Lead with "your documents and ACLs never leave your infrastructure", not "be compliant by August".

---

## 3. The repositioning: package, API surface, scope

### 3.1 What we extract and how we package it

pansoph already has the two pieces that constitute a permission layer:

1. `packages/pansoph-authz/` (the `AuthzPort`, the SpiceDB/OpenFGA/noop adapters, the ACL->tuple mappers).
2. The enforcement path in `packages/pansoph-retrieval/src/pansoph_retrieval/pipeline.py:18-75` (overfetch -> `bulk_check(at_least_as_fresh=token)` -> keep allowed subset -> apply `deny` as a final filter -> optional rerank).

**Current state:** both packages already declare independent, publishable status (`README.md:21-22`: "Each package under `packages/` is independently versioned and publishable to PyPI ... usable standalone"). The repositioning does not require a code rewrite. It requires (a) a public-facing identity for the permission layer as a *standalone product*, (b) a documented public API surface, and (c) a quickstart that demotes the 12-image stack.

**Proposed package identity.** Rather than invent a new PyPI distribution and fork maintenance, ship the permission layer as a **named, documented bundle** of the two existing packages with one thin convenience facade:

- **Positioning name:** **"pansoph-authz: the removable permission layer for RAG."** Keep the existing distribution name `pansoph-authz` as the front door (it is already the authz package and already publishable). Add `pansoph-retrieval` as the enforcement-path companion. Do not rename packages; rename the *story*.
- **Proposed thin facade (new, optional):** a single module `pansoph_authz.rag` that re-exports the enforcement entry point so an adopter writes one import, not three. This is sugar over existing code, not new behavior.
- **Engine-agnostic over the vector store:** the enforcement path depends only on the `VectorStore`/`Embedder`/`Reranker` ports (`packages/pansoph-retrieval/src/pansoph_retrieval/ports.py`), so an adopter who already has Pinecone/Weaviate/Chroma/Qdrant/pgvector implements one small adapter and keeps their store. We ship pgvector and Qdrant adapters; the contract is the port.
- **Engine-agnostic over the ReBAC backend:** the `AuthzPort` (`packages/pansoph-authz/src/pansoph_authz/port.py`) is satisfied today by SpiceDB and OpenFGA. An adopter can bring either, or `noop` to switch enforcement off.

### 3.2 The public API surface (what an adopter touches)

This is the contract we document and freeze for the standalone layer. All of it exists today; the work is to *document it as the product surface*, add the thin facade, and harden it with live tests.

```python
# 1. The enforcement entry point (exists: pansoph_retrieval/pipeline.py:18).
#    Overfetch -> bulk_check -> keep-allowed -> deny-as-final-filter -> rerank.
from pansoph_retrieval import retrieve

hits = await retrieve(
    query=query,
    subject=Principal(type="user", id="alice"),
    tenant_id="acme",
    subject_groups=["eng"],
    final_k=10,
    vector_store=my_store,   # any adapter of the VectorStore port (BYO store)
    embedder=my_embedder,    # any adapter of the Embedder port (BYO model)
    authz=my_authz,          # SpiceDB | OpenFGA | noop  (BYO ReBAC engine)
    reranker=None,           # optional
)

# 2. The authz port: the entire surface needed from a ReBAC engine
#    (exists: pansoph_authz/port.py). Three methods, no enumerate-all call.
from pansoph_authz import AuthzPort  # write_tuples, delete_tuples, bulk_check

# 3. The ACL -> tuple mappers: turn a source ACL into ReBAC tuples
#    (exists: pansoph_authz/mappers.py).
from pansoph_authz.mappers import (
    tuples_for_acl,      # one document's ACL  -> reader tuples
    tuples_for_document, # a Document          -> reader tuples
    membership_tuples,   # a user + groups     -> nested group-membership tuples
    diff_acls,           # (old, new) ACL      -> (to_write, to_delete) on a change
)

# 4. Consistency: the ZedToken/snapshot marker threaded so a revocation is
#    never read stale (the New Enemy Problem defense).
from pansoph_authz import ConsistencyToken
```

**The overfetch + filter recipe, stated as the documented invariant** (this is the thing we are selling, copied from `pipeline.py` and ADR-0002):

> Hybrid-search with a coarse pre-filter and overfetch (`OVERFETCH = 4`, so fetch `final_k * 4` candidates). The vector pre-filter only *shrinks* the candidate set; it is **never** the authorization gate. Then `bulk_check` is the **source of truth** for grants, read at `at_least_as_fresh` the freshest stored consistency token among the candidates. `deny` is applied as a **final filter**, never as an authz exclusion tuple (exclusion operators wreck bulk-check latency).

We must document, honestly, that this is a **post-filter-as-gate** architecture (the SpiceDB/Pinecone `CheckBulkPermissions` pattern, and what AWS Security recommends: verify at the source of truth, not the synced vector cache). It differs from Cerbos's **pre-filter-as-gate** Query-Plan approach. Both are valid. Our pitch: post-filter-as-gate guarantees the vector store can never leak through a stale metadata sync, because the authz engine, not the cache, decides.

### 3.3 In-scope vs out-of-scope for the standalone layer

**In scope (this is the permission layer):**

- The `AuthzPort` and its SpiceDB/OpenFGA/noop adapters.
- The ACL->tuple mappers and the ACL-diff (revocation) logic.
- The overfetch -> bulk_check -> deny-filter -> rerank enforcement path.
- The vector-store/embedder/reranker **ports** (the contract), plus pgvector and Qdrant **reference adapters**.
- The consistency-token threading (ZedToken / snapshot marker), the New Enemy Problem defense.
- Live integration tests against real SpiceDB/OpenFGA/pgvector (owned by Workstream B; a hard gate on the public claim).

**Out of scope (this stays in the full platform, not the layer):**

- Connectors (the 15 source connectors stay in `connectors/`, used by the platform; the layer ships *one* as a demo, not as a dependency).
- Durable ingestion, scheduling, the worker (`pansoph-ingestion`, currently a `worker.py:15` print placeholder, not wired).
- The FastAPI app, REST/MCP/SSE surface (`pansoph-core`, `agora`).
- The reference UI (`apps/lens`), the browser grabber (`apps/scout`), the MCP bridge (`relay`).
- Identity/OIDC, object store, secret store.
- The 12-image compose stack.

The discipline: the permission layer must be installable and usable **with zero pansoph platform services running**. If an adopter has to start `agora`, `valkey`, `seaweedfs`, or `keycloak` to enforce a permission check, the repositioning has failed.

---

## 4. New README / tagline language

The current README headline (`README.md:3`) is:

> "A self hostable, highly modular knowledge platform with ground up, removable, document level access control."

This leads with "knowledge platform" (the crowded, losing axis) and demotes the wedge to a trailing clause. It also contains the false "Status: Scaffold ... Nothing here ingests or retrieves yet" (`README.md:77`), contradicted by `apps`/`packages` code, and ADR links pointing outside the repo (`README.md:60-61`, `../architecture-decision-records/`, which 404 on GitHub). All three are separate fixes tracked in the launch-hygiene workstream; here we own only the **positioning language**.

### Candidate one-liners (lead with the wedge)

Pick one for the H1 tagline; the rest can seed social/landing copy.

1. **"The removable, engine-agnostic permission layer for RAG. Document-level access control that bolts onto any vector store, behind any ReBAC engine. Apache-2.0, self-host-first, no paywalled security."**
2. "Permission-aware retrieval as a library, not a platform. Alice cannot retrieve Bob's document, enforced at query time, removable, BYO vector store, BYO ReBAC engine."
3. "Document-level access control for your RAG stack, the part Onyx puts behind Enterprise. Open source, removable, engine-agnostic."
4. "Stop leaking documents through your vector store. pansoph enforces ReBAC at retrieval as the source of truth, so a stale metadata sync can never grant access."
5. "The authz layer that even your competitor's RAG stack can adopt. One port over SpiceDB or OpenFGA, one enforcement path over any vector store."

### Proposed above-the-fold structure (2026 README consensus)

- **H1:** `pansoph` + one-liner candidate 1.
- **Badge row** (small, curated): build/test, license (Apache-2.0), permission red-team gate, integration gate (once green from Workstream B). Group in a table only if it grows past 5.
- **One honest paragraph:** what it is, what it is *not* (a Glean clone).
- **5-line quickstart** (the single-command demo from section 5).
- **A "Non-goals" section** (maturity signal): "pansoph is not a Glean/Onyx platform clone. It does not aim to ship the most connectors. It ships the one part those platforms paywall or couple: removable document-level enforcement." This honesty doubles as positioning.
- **Never under-claim.** Remove "Status: Scaffold / Nothing here ingests or retrieves yet" and "Quick start (once implemented)"; replace with the real, demonstrated capability and an honest maturity line ("enforcement logic is red-team tested; live-engine integration tests landing in [link to Workstream B]").

---

## 5. The single demo spec

### 5.1 Connector choice: GitHub. Justified.

The demo must showcase **document-level ReBAC** with the least friction and the most legible ACL story. Candidates and the call:

| Connector | ACL fidelity (ground truth) | Demo friction | Verdict |
|---|---|---|---|
| **GitHub** | **Strong.** Real mapping: private-repo collaborators -> user reader tuples, teams -> group reader tuples, public repo -> wildcard. Nested team membership maps to nested `group#member`. See `connectors/github/src/pansoph_connector_github/connector.py:243-260` and `mappers.py`. | Low. A PAT and two repos, or fully mocked. No org-admin SSO dance. | **CHOSEN** |
| Google Drive / SharePoint | Conceptually ideal (per-file sharing). But ground truth: SharePoint "defers group expansion", `text=None` content; Drive requires OAuth consent + Workspace admin. High friction, weaker current fidelity. | High (OAuth, admin). | Rejected for v1 demo |
| Notion | Ground truth: "static operator-configured ACL". Not a live permission source. | Low but unconvincing. | Rejected |
| Salesforce | Ground truth: "ignores org-wide-defaults + role hierarchy". Weak exactly where ReBAC matters. | High. | Rejected |

GitHub wins on the only two axes that matter for a 5-minute proof: **real document-level ACL fidelity** (collaborators->users, teams->groups, public->wildcard, nested teams->nested groups, all of which the existing connector and mappers already implement) and **low setup friction**. It also mirrors the AuthZed/Docker reference demo's `document{owner, viewer, public}` shape, so reviewers who know that demo recognize the proof instantly.

> Honesty note: the existing GitHub connector had `incremental=False` set after the data-loss fix (commit `ccf56bf`), and content extraction across connectors is demo-grade. For this demo we either (a) use the live ACL path (which is the strong part) with a tiny fixed corpus, or (b) seed the corpus from a hermetic fixture so the demo never depends on connector content fidelity. The demo proves **enforcement**, not ingestion completeness, and the script must make that scope explicit.

### 5.2 The Alice-cannot-retrieve-Bob script

Scenario (mirrors the AuthZed/Docker SpiceDB-testcontainers RAG reference, adapted to GitHub semantics):

- Two GitHub-style repos in one connector instance:
  - `acme/secret-roadmap` (private): collaborator **bob** only.
  - `acme/handbook` (public): wildcard reader.
- Two users: **alice** (member of team `eng`), **bob** (collaborator on `secret-roadmap`).
- One document in `secret-roadmap` whose text answers the query "what is the Q3 roadmap"; one document in `handbook` that does not.

Acceptance script (the demo asserts all four):

- [ ] **Bob retrieves the roadmap.** `retrieve(query="Q3 roadmap", subject=bob, ...)` returns the `secret-roadmap` doc. (Authorized access works.)
- [ ] **Alice cannot.** `retrieve(query="Q3 roadmap", subject=alice, ...)` returns an empty or handbook-only result; the `secret-roadmap` doc is absent **even though it is the top vector hit** (the pre-filter overfetched it, the `bulk_check` denied it). This is the headline proof: relevance did not beat permission.
- [ ] **Public doc visible to all.** Both alice and bob retrieve `handbook` (wildcard reader).
- [ ] **Revocation is immediate and not stale.** Remove bob as collaborator (`diff_acls` -> `delete_tuples` -> fresh `ConsistencyToken`). The very next `retrieve(subject=bob, ...)` no longer returns the roadmap, because the read is `at_least_as_fresh` the revocation token. This demonstrates the New Enemy Problem defense, the thing metadata-filtering RAG stacks get wrong.

The script lives at `examples/github-rebac-demo/` (proposed new dir) as a single runnable Python file plus a tiny seed, and is **also** wired as a live integration test under `pytest -m integration` (so the demo and the test are the same code, killing the "demo overfits, test is fake" failure mode).

### 5.3 One-command quickstart (demote the 12-image stack)

The current `compose.yaml` pulls ~12 images / ~16 services (postgres/timescaledb-ha, valkey, seaweedfs, spicedb+migrate, keycloak+config, mcp-sample, agora, lens, scribe, ollama, tei-embed, tei-rerank). That is disqualifying for a 5-minute eval. The demo needs **at most one container**, ideally zero.

**Proposed: zero-to-one-container, two tiers.**

- **Tier 0 (zero containers, default, fastest):** the enforcement logic against an **in-process SpiceDB via testcontainers-spicedb** plus an **in-memory vector store** (the existing `FakeVectorStore` promoted to a tiny real `InMemoryVectorStore` reference adapter). The AuthZed testcontainers module spins a real SpiceDB `serve-testing` instance (isolated memdb per preshared key) for the duration of the run, so this is a **real ReBAC engine**, not a fake, with no compose file. One command:

```bash
export PATH="$HOME/.local/bin:$PATH"
uv run python examples/github-rebac-demo/demo.py
# Spins a real ephemeral SpiceDB (testcontainers), seeds 2 repos / 2 users,
# runs the 4 assertions above, prints the proof, tears everything down.
```

- **Tier 1 (one container, for "I want to keep it running"):** a single minimal compose profile, `--profile demo`, that starts **only** SpiceDB + pgvector-enabled Postgres (no valkey, seaweedfs, keycloak, ollama, tei, agora, lens, scribe). Embeddings come from a tiny local deterministic embedder fixture so the demo needs no model server.

```bash
docker compose --profile demo up -d        # 2 services, not 16
uv run python examples/github-rebac-demo/demo.py --live
docker compose --profile demo down
```

Acceptance: time from `git clone` to printed proof on a cold machine with `uv` and Docker installed is **under 5 minutes**, Tier 0 path, on a laptop. Measure it and record the number; do not assert it until measured (No fiction).

### 5.4 The proof the viewer sees

The demo prints a single, legible block. Example shape (illustrative, not yet real output):

```
pansoph permission layer demo (GitHub ReBAC, live SpiceDB)

query: "what is the Q3 roadmap"
top vector hit: document:github:acme:secret-roadmap  (relevance 0.91)

  bob   -> ALLOWED  -> returns [secret-roadmap, handbook]
  alice -> DENIED   -> returns [handbook]
          ^ secret-roadmap was the top hit and was filtered out by bulk_check,
            not by relevance. permission beat relevance.

revoking bob's access (delete_tuples -> token t2) ...
  bob   -> DENIED (read at_least_as_fresh t2) -> returns [handbook]
          ^ revocation visible on the very next query. no stale read.

PASS: 4/4 assertions. enforced by SpiceDB, not by the vector store.
```

The single load-bearing sentence a reviewer should leave with: **"the top-ranked, most relevant chunk was withheld because the authz engine, not the vector cache, is the source of truth, and a revocation took effect on the next query."**

---

## 6. Phased path

Sequenced so every milestone is independently shippable. No phase is "the full vertical". The Cognita lesson: do not bet the launch on a big-bang platform release.

### Week 1: positioning + the demo skeleton (no dependency on Workstream B)

- [ ] Rewrite the README above-the-fold to lead with the wedge (section 4). Remove the false "Scaffold / Nothing ingests or retrieves" and "once implemented" lines. Add a "Non-goals" section.
- [ ] Fix the ADR links to point in-repo (move ADRs to `docs/adr/NNNN-title.md` with relative links so they stop 404-ing on GitHub). Coordinate with the launch-hygiene workstream so this is not done twice.
- [ ] Add `examples/github-rebac-demo/` with the Tier 0 script running against **testcontainers-spicedb** plus the in-memory vector store. Promote `FakeVectorStore` to a documented `InMemoryVectorStore` reference adapter.
- [ ] The script asserts the first three demo checks (allow / deny / public). Revocation freshness (check 4) can land in the same week if testcontainers freshness behaves; otherwise it slips to month 1 with Workstream B.
- [ ] Measure cold-start time, record it honestly in the README.
- **Shippable milestone:** a README that sells the wedge + a one-command demo that runs a real SpiceDB and shows Alice denied. This alone is a credible "show HN".

### Month 1: make the security claim true, document the public surface

- [ ] **Hard dependency on Workstream B:** wire `pytest -m integration` to real SpiceDB (via `authzed/action-spicedb` in CI, replacing the `|| true` no-op in `.github/workflows/connector-live-smoke.yml`) and to OpenFGA, plus pgvector. The demo script *is* one of these integration tests.
- [ ] Add the revocation-freshness (New Enemy Problem) integration test with `at_least_as_fresh`, asserting a stale-read failure is caught.
- [ ] Add the pgvector pre-filter group-leak test (the specific bug class that would ship CI-green today).
- [ ] Write `docs/permission-layer.md`: the public API surface (section 3.2), the overfetch+filter recipe as the documented invariant, the post-filter-as-gate vs Cerbos pre-filter-as-gate honest comparison, and a "bring your own vector store" how-to (implement the `VectorStore` port).
- [ ] Add the thin `pansoph_authz.rag` facade (one-import convenience over the existing path).
- [ ] Publish `pansoph-authz` and `pansoph-retrieval` to PyPI as standalone, with the permission-layer positioning in their package descriptions.
- **Shippable milestone:** `pip install pansoph-authz pansoph-retrieval`, point at your own vector store + SpiceDB/OpenFGA, get enforced retrieval, with green live integration tests proving it. This is the actual product.

### Month 3: broaden adoption surface, prove neutrality

- [ ] Add one more vector-store reference adapter beyond pgvector/Qdrant if a real adopter asks (e.g. Chroma or Weaviate), driven by demand, not speculation.
- [ ] Write a 1-page "adopt pansoph as your permission layer on top of \<existing RAG stack\>" guide (the neutrality pitch: works alongside RAGFlow / a LangChain stack / Onyx CE, not instead of it).
- [ ] A second connector demo (Google Drive **if** its OAuth friction is solved by then; otherwise SharePoint group-expansion). Keep GitHub as the canonical one.
- [ ] Pursue OpenSSF Best Practices passing badge and run the Scorecard (self-cert, free, achievable solo on the day-one-passable checks: License, Security-Policy, SAST, Pinned-Dependencies, CI-Tests, etc.). A security-positioned project that fails its own security hygiene is not credible.
- **Shippable milestone:** a documented, badge-bearing, multi-engine permission layer with two demos and an adoption guide that names how to bolt it onto a competitor's stack.

---

## 7. Risks and kill-criteria

### Risks

- **Commoditization by the primitive vendors.** SpiceDB or OpenFGA ships a first-party `*-rag` library and absorbs the differentiation. *Mitigation:* be the neutral, engine-agnostic, Apache-2.0 option that spans both; move first; make adoption trivial.
- **The wedge is too thin to build a community.** Document-level ReBAC for RAG may be a feature, not a project. *Mitigation:* the engine-agnostic + removable + OSS combination is the project; if adopters only want the recipe, we still win mindshare and can fold it back into the platform.
- **The security claim outruns the proof.** If we market "proven enforcement" before Workstream B's live tests are green, one found group-leak bug destroys the entire security positioning. *Mitigation:* the No-fiction rule. Do not claim live-engine proof until `pytest -m integration` is genuinely green against real SpiceDB/OpenFGA/pgvector. This is the single highest-stakes discipline in the plan.
- **Demo overfit.** A scripted demo that proves nothing about generality. *Mitigation:* the demo *is* an integration test; it runs against a real engine; it asserts revocation freshness, not just a happy path.

### Kill-criteria (signals that say "this wedge is not working, pivot again")

- **By end of month 1:** if the live integration tests (Workstream B) cannot be made green against a real SpiceDB/OpenFGA without architectural rework that breaks the `AuthzPort` contract, the "engine-agnostic" claim is false and the whole repositioning is unsound. **Stop and re-scope to a single engine, or pivot.**
- **By end of month 3:** if a "show HN" or equivalent launch of the standalone permission layer draws **fewer than ~50 stars and zero external `pip install` adopters or issues**, the market does not want a removable permission primitive separate from a platform. Reconsider folding back into a platform play, or shelve.
- **At any point:** if SpiceDB or OpenFGA ships a maintained, Apache-2.0, engine-agnostic RAG-enforcement library that spans multiple vector stores, the empty cell is filled by a better-resourced incumbent. The wedge is gone. Pivot to integration/connectors or shelve.
- **At any point:** if a real adopter cannot get the demo to print PASS in under 10 minutes on a clean machine, the "5-minute quickstart" promise is broken and the launch hook fails. Fix the quickstart before any public launch.

---

## Sources (verified 2026-06-23, re-verified 2026-06-25)

- Onyx document-level RBAC is Enterprise-gated (the wedge): <https://docs.onyx.app/security/architecture/access_controls> and the GitHub connector permission-sync gating <https://docs.onyx.app/admins/connectors/official/github>
- Onyx repo (30,519 stars, NOASSERTION = MIT CE + EE, v4.2.0 2026-06-22, curl|bash install): <https://github.com/onyx-dot-app/onyx>
- Glean $7.2B / ~$300M ARR (TechCrunch, 2026-05-28): <https://techcrunch.com/2026/05/28/gleans-top-line-crosses-300m-as-ai-budget-cutting-becomes-its-major-selling-point/>
- RAGFlow (~83,540 stars, Apache-2.0): <https://github.com/infiniflow/ragflow>
- PipesHub (~3.0k stars, Apache-2.0, Glean alternative): <https://github.com/pipeshub-ai/pipeshub-ai>
- Cognita ARCHIVED 2026-03-13 (~4,408 stars, Apache-2.0, "no longer actively maintained"): <https://github.com/truefoundry/cognita>
- Cerbos pre-filter / Query-Plan AST for RAG over vector stores: <https://www.cerbos.dev/features-benefits-and-use-cases/access-control-for-rag>
- OpenFGA RAG-authorization patterns (ListObjects pre-filter, BatchCheck post-filter, hybrid): <https://openfga.dev/docs/use-cases/rag-authorization>
- Ory rerag-rebac (closest OSS analog, a demo repo, not a library): <https://github.com/ory/rerag-rebac>
- Pinecone "RAG with Access Control" (pre-filter vs post-filter tradeoff): <https://www.pinecone.io/learn/rag-access-control/>
- AWS Security Blog, verify at the data source, not the synced vector cache: <https://aws.amazon.com/blogs/security/authorizing-access-to-data-with-rag-implementations/>
- SpiceDB consistency (at_least_as_fresh, ZedToken, New Enemy Problem): <https://authzed.com/docs/spicedb/concepts/consistency>
- SpiceDB serve-testing / commands (isolated memdb per preshared key): <https://authzed.com/docs/spicedb/concepts/commands>
- AuthZed testcontainers RAG testing doc: <https://authzed.com/docs/spicedb/integrations/testcontainers>
- Docker blog, RAG permission testing with testcontainers + SpiceDB (reference schema and isolation assertions): <https://www.docker.com/blog/rag-permission-testing-testcontainers-spicedb/>
- authzed/action-spicedb GitHub Action (replaces the `|| true` CI no-op): <https://github.com/authzed/action-spicedb>
- Zanzibar paper (New Enemy Problem, zookies): <https://www.usenix.org/system/files/atc19-pang.pdf>
- EU AI Act GPAI enforcement powers / Article 101 from 2 Aug 2026, open-licence reductions, Digital Omnibus high-risk deferral to 2 Dec 2027: <https://www.lw.com/en/insights/eu-ai-act-gpai-model-obligations-in-force-and-final-gpai-code-of-practice-in-place>
- README best practice (lead with what it does, do not under-claim): <https://www.makeareadme.com/>
- OpenSSF Best Practices criteria (passing badge, self-cert): <https://www.bestpractices.dev/en/criteria/0>
- OSS Scorecard checks (day-one-passable set): <https://github.com/ossf/scorecard/blob/main/docs/checks.md>

### pansoph code referenced (current state, verified in-repo 2026-06-25)

- Enforcement path: `packages/pansoph-retrieval/src/pansoph_retrieval/pipeline.py:18-75`
- AuthzPort: `packages/pansoph-authz/src/pansoph_authz/port.py`
- ACL->tuple mappers: `packages/pansoph-authz/src/pansoph_authz/mappers.py`
- SpiceDB adapter + schema: `packages/pansoph-authz/src/pansoph_authz/adapters/spicedb.py`
- GitHub connector ACL (collaborators/teams/public): `connectors/github/src/pansoph_connector_github/connector.py:243-260`
- Permission red-team (in-memory fakes only, the gap): `tests/permission/test_redteam.py`
- CI no-op to replace: `.github/workflows/connector-live-smoke.yml`
- Current README headline/false-status/external ADR links: `README.md:3,60-61,77`
