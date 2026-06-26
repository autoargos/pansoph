# Launch-Prep Research Sources (verified 2026-06-23 / 2026-06-25)

This document is the consolidated, citation-rich research backing for the three
pansoph launch-prep workstreams. It exists so the plans stay grounded and
re-checkable: every external claim in those plans should trace to a primary
source listed here, and every competitor number, license tier, API name, and
regulatory date that the plans rest on has been fact-checked below.

What was researched, and why:

1. **Launch readiness and OSS honesty.** What a security-positioned, Apache-2.0,
   self-hosted project must ship on day one (SECURITY.md and coordinated
   vulnerability disclosure, community-health files, OpenSSF Scorecard and Best
   Practices Badge, README maturity framing, ADR publishing, Apache NOTICE
   handling) so the launch is credible and does not over-claim.
2. **Enforcement integration testing and SpiceDB consistency.** How to test
   permission-aware RAG retrieval against a real SpiceDB engine, the consistency
   semantics (ZedToken, `at_least_as_fresh`, the New Enemy Problem), and the
   pre-filter vs post-filter authorization debate.
3. **Permission-layer repositioning and market.** Competitive and regulatory
   evidence for positioning pansoph as "the removable permission layer for RAG"
   (Onyx, Cognita, Glean, Cerbos, OpenFGA/Auth0 FGA, Ory rerag-rebac, PipesHub,
   RAGFlow), and the EU AI Act timeline.

Research window: web verification performed 2026-06-23 through 2026-06-25.
Where a competitor metric drifted between the digest and live re-verification on
2026-06-25, the live number is recorded in the Fact-check corrections section and
should be treated as canonical.

Conventions: no em dashes; Apache-2.0, self-host-first, model-agnostic framing;
no fiction (current state vs proposed is marked explicitly in the plans, not
here). Primary sources are preferred throughout.

---

## 1. Launch readiness and OSS honesty

### Key findings

**SECURITY.md and Private Vulnerability Reporting are independent and should be
paired.** GitHub Private Vulnerability Reporting (PVR) is separate from a
repository's `SECURITY.md` file, and GitHub recommends enabling both. PVR is a
one-click toggle under Settings > (Code security and) Advanced Security >
Private vulnerability reporting > Enable. It surfaces a "Report a vulnerability"
button and, on submission, auto-adds the reporter as a credited collaborator on
the draft advisory. Two caveats to carry into the plan: PVR is available for
**public repositories only**, and maintainers do **not** get notifications by
default (you must Watch > Custom > Security alerts).

**A credible SECURITY.md contains a defined content set, and the OpenSSF OSPS
Baseline now makes a CVD policy mandatory for active projects.** The file should
state scope/supported versions, a private reporting channel (prefer PVR with a
`security@` fallback), an acknowledgement SLA (sigstore uses 24 hours), the
coordinated vulnerability disclosure (CVD) workflow, an optional GPG/age key, and
safe-harbor language. The OpenSSF OSPS Baseline (version dated 2025-10-10)
requires an active project to document a CVD policy with a clear response
timeframe and recommends `SECURITY.md` at the repo root.

**RFC 9116 `security.txt` has exactly two mandatory fields.** Served at
`/.well-known/security.txt` over HTTPS, only `Contact` and `Expires` are
required. `Expires` must be a single RFC 3339 / ISO 8601 datetime with a timezone
offset (for example `2026-12-31T23:59:59Z`), recommended at most one year out.
`Acknowledgments` uses the US spelling. The full field set is `Contact`,
`Expires`, `Encryption`, `Acknowledgments`, `Preferred-Languages`, `Canonical`,
`Policy` (plus `Hiring`). OpenPGP cleartext signing is RECOMMENDED, not required.
The RFC explicitly says not having the file may be preferable to having stale
information.

**For a solo Apache-2.0 project with no relicensing plan, DCO beats CLA.** The
Developer Certificate of Origin is a per-commit `Signed-off-by` line added by
`git commit -s`, lower friction, and sufficient for permissive, stable licenses.
A CLA only earns its friction when you want unilateral relicensing rights or a
separate commercial/enterprise edition. OpenSSF Best Practices explicitly names
DCO as "the most common and easily-implemented approach" for the
contributor-authorization criterion. (CNCF default and Spring's 2025 CLA-to-DCO
migration are corroborating but secondary.)

**Contributor Covenant is the de-facto CODE_OF_CONDUCT, and 3.0 now exists.**
Contributor Covenant 3.0 was released 2025-07-28 by the Organization for Ethical
Source: a major rewrite that renames the enforcement section to "Addressing and
Repairing Harm" (restorative-justice framing) with a revised enforcement ladder
and a customization builder. Version 2.1 remains widely deployed. Pin a specific
version and supply a real enforcement contact email.

**OpenSSF Scorecard: roughly 8 to 10 checks are passable on day one for a solo
repo.** The day-one set is License, Security-Policy, Binary-Artifacts,
Dangerous-Workflow, Token-Permissions, SAST, Pinned-Dependencies,
Dependency-Update-Tool, CI-Tests, and Vulnerabilities. History-gated checks that
will not pass until time, a second reviewer, or a published signed release accrue
are Code-Review, Contributors, Maintained, CII/OpenSSF-Best-Practices,
Signed-Releases, and Packaging. Branch-Protection is also gated on repo
settings/admin token. Note the public weekly Scorecard dataset omits
CI-Tests/Contributors/Dependency-Update-Tool for cost, so run the binary yourself
to see those.

**OpenSSF Best Practices "passing" badge is self-certified and free, but
deliberate.** Available at bestpractices.dev. To pass: all MUST/MUST-NOT met, all
SHOULD met-or-documented-rationale, all SUGGESTED rated met/unmet. It explicitly
requires the project website to succinctly describe what the software does and to
explain how to obtain it, give feedback, and contribute. The "~10% pass" figure
is a rough "hard but doable" signal, not a precise current statistic (a public
snapshot showed 1019 passing of 5735 registered, about 18%; the share that
achieve passing among all who start is lower).

**README consensus for 2026:** honest above-the-fold content (a self-descriptive
name, a one-line "X is a Y that does Z", a small curated badge row of
build/test/license/coverage grouped into a table if more than five, and a roughly
five-line quickstart), depth below, an optional "Non-goals" section as a maturity
signal, and the core rule never to under-claim. The "Non-goals as a maturity
signal" point is reasonable convention rather than a normative spec.

**ADRs belong in-repo under version control.** Nygard's 2011 convention stores
ADRs at `doc/adr/NNNN-title.md` with Title/Status/Context/Decision/Consequences,
sequential numbers never reused, and superseded ADRs kept-and-marked rather than
deleted. MADR 4.0 adds explicit Considered Options with pros/cons and recommends
co-locating ADRs with code. Practical takeaway for pansoph: README ADR links
pointing outside the repo will 404 on GitHub and must be moved in-repo with
relative links.

**An Apache-2.0 NOTICE file is optional for a non-ASF solo project.** Apache infra
docs state the NOTICE file's presence is "not mandated by the license itself, but
by ASF policy." For a non-ASF project like pansoph, NOTICE is optional. You only
need NOTICE content additions when you bundle/redistribute a work that legally
requires attribution there (for example a dependency shipping its own NOTICE).
Most permissive (MIT/BSD) and plain Apache-2.0 deps need only a LICENSE pointer.
Keep NOTICE lean; do not dump the dependency list into it. This conclusion holds
as long as pansoph is not framed as an ASF project.

**Competitive precedent supporting the wedge:** Onyx document-level permission
sync is Enterprise/Cloud-gated (the GitHub-connector permission sync is
"available only on Cloud and the Enterprise Edition"), so the MIT community tier
lacks it and the removable-document-level-ReBAC wedge holds. Cognita
(truefoundry/cognita) **was archived on 2026-03-13** (verified against the repo
banner), so it may be kept as a load-bearing competitive precedent.

### SOURCES (Launch readiness and OSS honesty)

| Title | URL | Date | Key fact |
|---|---|---|---|
| GitHub Docs: Configuring private vulnerability reporting | https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/configuring-private-vulnerability-reporting-for-a-repository | 2026 | PVR is separate from SECURITY.md; enable both; public repos only; reporter auto-credited; no default notifications |
| OpenSSF OSPS Baseline (version 2025-10-10) | https://baseline.openssf.org/versions/2025-10-10.html | 2025-10-10 | Active projects MUST document a CVD policy with a stated response timeframe; SECURITY.md at repo root recommended |
| RFC 9116 (security.txt) | https://www.rfc-editor.org/info/rfc9116/ | 2022 | Only Contact and Expires mandatory; Expires is RFC 3339 with timezone; served at /.well-known/security.txt over HTTPS; "Acknowledgments" US spelling |
| OpenSSF Best Practices criteria (passing) | https://www.bestpractices.dev/en/criteria/0 | 2026 | Self-certified free; DCO named as most common contributor-auth approach; website must describe software and contribution path |
| Contributor Covenant 3.0 announcement | https://ethicalsource.dev/blog/contributor-covenant-3/ | 2025-07-28 | v3.0 released, "Addressing and Repairing Harm" section, revised enforcement ladder; pin a version |
| OpenSSF Scorecard checks list | https://github.com/ossf/scorecard/blob/main/docs/checks.md | 2026 | 18 checks; day-one-passable vs history-gated split; public dataset omits some checks |
| MADR / ADR templates (adr.github.io) | https://adr.github.io/adr-templates/ | 2026 | ADRs in-repo, sequential never-reused numbers, superseded kept-and-marked; MADR 4.0 adds Considered Options |
| Apache licensing how-to (NOTICE policy) | https://infra.apache.org/licensing-howto.html | 2026 | NOTICE presence is ASF policy not license law; optional for non-ASF projects; keep lean |
| makeareadme (README best practice) | https://www.makeareadme.com/ | 2026 | Self-descriptive name, concise one-liner, small curated badge row, short quickstart, do not under-describe |
| Onyx GitHub connector docs (permission sync gating) | https://docs.onyx.app/admins/connectors/official/github | 2026 | Permission sync available only on Cloud and Enterprise Edition; MIT/community tier lacks it |
| truefoundry/cognita (archived) | https://github.com/truefoundry/cognita | 2026-03-13 | Repo archived 2026-03-13, read-only, "no longer actively maintained", ~4.4k stars |
| Latham & Watkins: EU AI Act GPAI obligations | https://www.lw.com/en/insights/eu-ai-act-gpai-model-obligations-in-force-and-final-gpai-code-of-practice-in-place | 2026 | GPAI enforcement powers (Article 101 fines up to 3% turnover / EUR15M) activate 2 Aug 2026; free-and-open-licence reduced obligations |

---

## 2. Enforcement integration testing and SpiceDB consistency

### Key findings

**SpiceDB has four consistency modes, and `at_least_as_fresh` is the
recommended one for revocation safety.** `minimize_latency` is the default with a
roughly 5-second enforced max staleness and can expose the New Enemy Problem.
`at_least_as_fresh` behaves like `minimize_latency` unless the selected timestamp
is older than the supplied ZedToken, in which case the chosen timestamp is
adjusted forward to the ZedToken's timestamp. The other modes are
`at_exact_snapshot` and `fully_consistent` (which bypasses cache). AuthZed
best-practices recommend `at_least_as_fresh` over `fully_consistent`.

**ZedTokens are returned from the write and check APIs and are the New-Enemy
defeat mechanism.** `WriteRelationships` returns a ZedToken in `written_at` and
`DeleteRelationships` returns one in `deleted_at`. ZedTokens are also returned by
`CheckPermission` and `CheckBulkPermissions`. ZedToken is documented as the
SpiceDB equivalent of Zanzibar's zookie that protects against the New Enemy
Problem. The recommended revocation pattern: store the ZedToken on the
content write/change and check with `at_least_as_fresh` so later revocations are
guaranteed visible.

**`spicedb serve-testing` is the isolated in-memory integration server.** It
gives each unique preshared key its own isolated empty memdb datastore, enabling
parallel tests; it runs on port 50051 with a read-only replica on 50052.
Documented caveats: memory grows monotonically (the server never deletes data, so
restart between suite runs), and heavy parallel writes trigger "serialization max
retries exceeded; please reduce your parallel writes", so keep one in-flight write
per test.

**Official and community Testcontainers integrations exist.** The first-party
AuthZed doc "Testing RAG Pipelines with Testcontainers and SpiceDB" shows the
exact Python snippet (image `authzed/spicedb:v1.47.1`, `InsecureClient` over
`get_endpoint()`/`get_secret_key()`, `WriteSchema`, `CheckPermission`). A
Testcontainers SpiceDB module is published for both Python
(`pip install testcontainers-spicedb`) and Go. Each test gets a fresh, isolated
authorization graph.

**Reference repos demonstrate the seed-and-isolate pattern.** The Docker blog "RAG
Permission Testing with Testcontainers and SpiceDB" links the Go reference repo
`sohanmaheshwar/spicedb-testcontainer-rag` with the schema
`document { relation owner: user; relation viewer: user | owner; permission read = owner + viewer }`.
It seeds 3 users (Emilia/Beatrice/Charlie) and 3 docs (doc1 owner, doc2 viewer,
doc3 public to all three) and asserts cross-user isolation, with the public doc
visible to all. The two-stage pipeline is trivial retrieval then post-filter via
`CheckPermission` (assert `permissionship == HAS_PERMISSION`).

**Pre-filter vs post-filter is a selectivity decision.** Pinecone's "RAG with
Access Control" contrasts pre-filter (`LookupResources`) vs post-filter
(`CheckPermission`/`CheckBulkPermissions`). `LookupResources` is computationally
more expensive than a `Check`. Choose post-filter when the positive hit-rate is
high; choose pre-filter when the corpus is large and the hit-rate is low. The
article adds the practical caveat to inspect your own SpiceDB metrics rather than
apply a one-size-fits-all rule.

**The vector store is not an authorization source of truth.** The AWS Security
Blog "Authorizing access to data with RAG implementations" argues vector-DB
metadata filtering is a baseline only, not a strong authorization source of truth,
because stores sync periodically so revocations lag. Permissions should be
verified at the data source, not at an intermediate synced cache. This supports
the pansoph invariant that the vector pre-filter only shrinks candidates while
`authz.bulk_check` is the source of truth.

**Cerbos advocates "enforce before augmentation", but via pre-filtering as the
gate.** Cerbos's principle is that unauthorized content should never enter the
prompt. Its actual mechanism is pre-filtering via a portable Query Plan (an AST
translated into native vector-store filters for Pinecone/Weaviate/Chroma/Qdrant/FAISS),
so Cerbos treats the pre-filter AS the gate. This differs from pansoph's stated
design where the pre-filter is a candidate-shrinker and `authz.bulk_check` is the
final source-of-truth gate (a post-filter model closer to the
SpiceDB/Pinecone `CheckBulkPermissions` pattern). The "enforce before
augmentation" principle is confirmed; pansoph's post-filter-as-gate is a
different (also valid) implementation choice, not the one Cerbos itself
recommends.

**The New Enemy Problem is foundational, non-stale Zanzibar material.** The
Zanzibar paper (USENIX ATC 2019) defines the New Enemy Problem and solves it with
zookies (per-content opaque consistency tokens encoding a timestamp at/after prior
ACL writes, giving a lower bound on freshness). Two canonical failure modes: a
stale ACL applied to new content, and out-of-order ACL updates via folder
inheritance. ZedToken plus `at_least_as_fresh` is the SpiceDB mitigation.

**There is an official CI action.** `authzed/action-spicedb` is a published AuthZed
GitHub Action that launches a SpiceDB server for integration testing in CI (used
via `uses: authzed/action-spicedb` with a version input), running the same
`serve-testing` integration server. This is suitable for replacing the
`connector-live-smoke.yml` `|| true` no-op. Note: the action's entrypoint was not
directly inspected in this pass, so treat the port 50051 / memdb specifics as
docs-attested rather than source-verified.

### SOURCES (Enforcement integration testing and SpiceDB consistency)

| Title | URL | Date | Key fact |
|---|---|---|---|
| AuthZed: SpiceDB consistency concepts | https://authzed.com/docs/spicedb/concepts/consistency | 2026 | Four modes; at_least_as_fresh adjusts forward to ZedToken; minimize_latency ~5s staleness; ZedToken defeats New Enemy Problem; returned from Write/Delete/Check |
| AuthZed: SpiceDB commands (serve-testing) | https://authzed.com/docs/spicedb/concepts/commands | 2026 | serve-testing = isolated memdb per preshared key, port 50051 / read-only 50052; serialization-retry caveat |
| AuthZed: Testing RAG Pipelines with Testcontainers and SpiceDB | https://authzed.com/docs/spicedb/integrations/testcontainers | 2026 | Python snippet with authzed/spicedb:v1.47.1, InsecureClient, WriteSchema, CheckPermission; fresh isolated graph per test |
| Docker blog: RAG Permission Testing with Testcontainers and SpiceDB | https://www.docker.com/blog/rag-permission-testing-testcontainers-spicedb/ | 2026 | Go reference repo with document schema; 3 users / 3 docs; asserts cross-user isolation and public-doc visibility |
| Pinecone: RAG with Access Control | https://www.pinecone.io/learn/rag-access-control/ | 2026 | Pre-filter (LookupResources) vs post-filter (Check); LookupResources costlier; choose by hit-rate; inspect your own metrics |
| AWS Security Blog: Authorizing access to data with RAG implementations | https://aws.amazon.com/blogs/security/authorizing-access-to-data-with-rag-implementations/ | 2026 | Vector-DB metadata filtering is baseline only; periodic sync lags revocations; verify at the data source |
| Cerbos: Access control for RAG | https://www.cerbos.dev/features-benefits-and-use-cases/access-control-for-rag | 2026 | Enforce-before-augmentation via Query Plan AST translated to native vector-store filters (pre-filter as gate) |
| Zanzibar paper (USENIX ATC 2019) | https://www.usenix.org/system/files/atc19-pang.pdf | 2019 | Defines New Enemy Problem; solves with zookies (consistency tokens, freshness lower bound) |
| authzed/action-spicedb (GitHub Action) | https://github.com/authzed/action-spicedb | 2026 | Official Action runs serve-testing in CI; replaces the `|| true` smoke no-op |

---

## 3. Permission-layer repositioning and market

### Key findings

**Onyx gates document-level access control to the paid Enterprise Edition.**
Onyx's official docs state that "RBAC protection to documents and resources are
only available in the Enterprise Edition of Onyx," that "Different access to
documents is only available in the Enterprise Edition of Onyx," and that the
"Curator: Document management and content curation" role is Enterprise-only. The
Community Edition core is MIT-licensed but lacks document-level RBAC. The wedge is
real and current.

**Onyx is large and consumer-installable.** `onyx-dot-app/onyx` is at about 30,519
stars (verified 2026-06-25; the digest said ~30,495, minor upward drift), 4,187
forks, license reported as `NOASSERTION` (dual MIT Community Edition core plus a
custom Enterprise Edition), latest release v4.2.0 dated 2026-06-22. The README
leads with the one-command `curl -fsSL https://onyx.app/install_onyx.sh | bash`
install and advertises "50+ indexing-based connectors out of the box or via MCP."

**Cognita is archived, validating the "modular RAG platform" thesis as a weak
position.** `truefoundry/cognita` is archived (owner archived it 2026-03-13), at
about 4,408 stars (digest said 4,411; negligible drift), Apache-2.0, with a top
banner reading "This project is no longer actively maintained. We thank all
contributors for their contributions." This supports dropping the "modular
knowledge platform" framing.

**The platform axis is not winnable.** Glean reached $300M ARR (TechCrunch,
2026-05-28), a three-fold increase from the $100M milestone reached 15 months
earlier, at a $7.2B valuation from a $150M Series F (announced June 2025, led by
Wellington). Caveat: TechCrunch notes the $300M figure is partly consumption-based
(annualized run rate) rather than strictly recurring. The conclusion stands: do
not compete on the platform axis.

**The durable regulatory tailwind is sovereignty/self-hosting, not a high-risk
deadline.** On 2 Aug 2026 the EU AI Act activates the Commission's GPAI
enforcement powers and Article 101 penalties (up to 3% of worldwide turnover or
EUR15M, whichever is higher). GPAI obligations themselves have applied since 2 Aug
2025; models on the market before 2 Aug 2025 must comply by 2 Aug 2027.
Free-and-open-licence GPAI providers are exempted from the Article 53(1)(a)(b)
technical-documentation/downstream-info duties and the Article 54(6) EU-rep duty,
which favors Apache-2.0/self-host/BYO. Copyright-policy and training-data-summary
obligations always remain, and the carve-out fully evaporates for systemic-risk
models (above 10^25 FLOPs). The Digital Omnibus on AI (provisional agreement
2026-05-07; Parliament endorsed 2026-06-16; Council adoption / OJ publication
expected July 2026, not yet fully formally adopted as of 2026-06-25) defers Annex
III high-risk obligations from 2 Aug 2026 to 2 Dec 2027. The framing caveat is
correct: lead with sovereignty/self-hosting, not a high-risk compliance deadline.

**The "removable, engine-agnostic OSS enforcement library" niche is open.** The
incumbents deliver authz-for-RAG as vendor SDK glue, not a packaged removable
Apache-2.0 library:

- **Cerbos** enforces via its `PlanResources`/Query-Plan API, returning a
  conditional AST (`KIND_CONDITIONAL`) translated into native filter syntax for
  Pinecone, Weaviate, Chroma, Qdrant, and FAISS, before retrieval. Delivered as
  commercial-vendor SDK glue (for example the LangGraph + Pinecone recipe), not a
  removable engine-agnostic library.
- **OpenFGA / Auth0 FGA** documents three patterns (pre-filter via `ListObjects`,
  post-filter via `BatchCheck`, hybrid) for ReBAC over vector DBs across Java
  (LangChain4j), .NET, and Python/TypeScript. This validates pansoph's
  overfetch -> `bulk_check` -> filter path as the industry-consensus pattern, but
  it ships as vendor SDK glue, not a standalone Apache-2.0 library.
- **Ory rerag-rebac** is the closest OSS analog (Zanzibar-style ReBAC for RAG via
  Ory Keto plus SQLite-vec) but is a small reference/demo repo (34 stars, 2 forks,
  no SPDX license set, last pushed 2025-11-13), not a reusable engine-agnostic
  library.
- **PipesHub** is an Apache-2.0 "open source Glean alternative" at about 2,999
  stars (verified 2026-06-25; digest said ~2,796, update to ~3.0k), 466 forks,
  doing permission-aware search with source-ACL inheritance across 30+ connectors,
  but its ACL inheritance is connector-coupled rather than a removable
  engine-agnostic primitive.
- **RAGFlow** has 83,540 stars (verified 2026-06-25; matches the ~83.4k estimate),
  Apache-2.0: the top of the OSS RAG-engine category is saturated, so
  differentiation must come from the authz/permission axis, not RAG breadth.

### SOURCES (Permission-layer repositioning and market)

| Title | URL | Date | Key fact |
|---|---|---|---|
| Onyx access-controls architecture docs | https://docs.onyx.app/security/architecture/access_controls | 2026-06-25 | Document RBAC, ACL-mirroring, Curator role all Enterprise-only; Community Edition (MIT) lacks document-level RBAC |
| onyx-dot-app/onyx (GitHub) | https://github.com/onyx-dot-app/onyx | 2026-06-25 | ~30,519 stars, NOASSERTION (MIT CE + custom EE), v4.2.0 (2026-06-22), curl-bash install, 50+ connectors |
| truefoundry/cognita (GitHub, archived) | https://github.com/truefoundry/cognita | 2026-03-13 | Archived 2026-03-13, ~4,408 stars, Apache-2.0, "no longer actively maintained" banner |
| TechCrunch: Glean crosses $300M ARR | https://techcrunch.com/2026/05/28/gleans-top-line-crosses-300m-as-ai-budget-cutting-becomes-its-major-selling-point/ | 2026-05-28 | $300M ARR (3x from $100M 15 months earlier), $7.2B valuation, $150M Series F; ARR partly consumption-based |
| Latham & Watkins: EU AI Act GPAI obligations | https://www.lw.com/en/insights/eu-ai-act-gpai-model-obligations-in-force-and-final-gpai-code-of-practice-in-place | 2026 | 2 Aug 2026 activates Article 101 fines (3% turnover / EUR15M); Digital Omnibus defers Annex III high-risk to 2 Dec 2027; open-licence carve-outs |
| Cerbos: Access control for RAG | https://www.cerbos.dev/features-benefits-and-use-cases/access-control-for-rag | 2026 | PlanResources Query Plan returns AST translated to native vector-store filters; vendor SDK glue, not removable OSS library |
| OpenFGA: RAG authorization | https://openfga.dev/docs/use-cases/rag-authorization | 2025-2026 | Pre-filter (ListObjects), post-filter (BatchCheck), hybrid; validates overfetch -> bulk_check -> filter; ships as SDK glue |
| ory/rerag-rebac (GitHub) | https://github.com/ory/rerag-rebac | 2025-11-13 | Closest OSS analog (Keto + SQLite-vec); 34 stars, demo/reference repo, niche open |
| pipeshub-ai/pipeshub-ai (GitHub) | https://github.com/pipeshub-ai/pipeshub-ai | 2026-06-25 | Apache-2.0, ~2,999 stars, source-ACL inheritance across 30+ connectors; connector-coupled, not removable primitive |
| infiniflow/ragflow (GitHub) | https://github.com/infiniflow/ragflow | 2026-06-25 | Apache-2.0, 83,540 stars; OSS RAG-engine category saturated at the top |

---

## Fact-check corrections (as of 2026-06-23)

These are verified corrections that must override any stale figures in the plans.
Each carries its source URL.

1. **Cognita IS archived (correcting a prior "do not treat as archived"
   correction).** A later digest "correction" claimed fresh search did not confirm
   Cognita was archived. That correction is itself wrong. The GitHub repo shows a
   banner: "This repository was archived by the owner on Mar 13, 2026. It is now
   read-only," with "no longer actively maintained" in the description (~4.4k
   stars, ~388 forks). pansoph MAY keep "Cognita archived (2026-03-13)" as a
   load-bearing competitive precedent. Early-2026 PRs/issues predate the archival
   and do not indicate ongoing activity.
   Source: https://github.com/truefoundry/cognita

2. **Onyx star count: ~30,519 (not ~30,495).** Minor upward drift verified
   2026-06-25; 4,187 forks; license `NOASSERTION` (MIT CE + custom EE); latest
   release v4.2.0 dated 2026-06-22; "50+ connectors".
   Source: https://github.com/onyx-dot-app/onyx

3. **Cognita star count: ~4,408 (not 4,411).** Negligible drift; Apache-2.0;
   archived 2026-03-13.
   Source: https://github.com/truefoundry/cognita

4. **PipesHub star count: ~2,999 / round to ~3.0k (not ~2,796).** Modest upward
   drift verified 2026-06-25; Apache-2.0; 466 forks; ACL inheritance is
   connector-coupled, not a removable primitive.
   Source: https://github.com/pipeshub-ai/pipeshub-ai

5. **RAGFlow star count: 83,540 (matches ~83.4k estimate).** Apache-2.0; 9,681
   forks; category saturated.
   Source: https://github.com/infiniflow/ragflow

6. **Glean: $300M ARR, $7.2B valuation, $150M Series F.** The $300M is a 3x rise
   from $100M reached 15 months earlier; the $300M figure is partly
   consumption-based (annualized run rate), not strictly recurring ARR.
   Source: https://techcrunch.com/2026/05/28/gleans-top-line-crosses-300m-as-ai-budget-cutting-becomes-its-major-selling-point/

7. **Onyx license/permission tier: document-level RBAC, ACL-mirroring, and the
   Curator role are Enterprise-only; Community Edition is MIT and lacks
   document-level RBAC.** Verbatim from docs.onyx.app (fetched 2026-06-25).
   Source: https://docs.onyx.app/security/architecture/access_controls

8. **EU AI Act dates.** GPAI obligations apply since 2 Aug 2025. Article 101
   enforcement/penalty powers (fines up to 3% worldwide turnover or EUR15M,
   whichever higher) activate 2 Aug 2026. Pre-2 Aug 2025 models must comply by 2
   Aug 2027. The Digital Omnibus defers Annex III high-risk obligations from 2 Aug
   2026 to 2 Dec 2027 (provisional agreement 2026-05-07; Parliament endorsed
   2026-06-16; Council adoption / OJ publication expected July 2026, NOT yet fully
   formally adopted as of 2026-06-25). The Digital Omnibus deferral does NOT touch
   the GPAI fine-enforcement trigger of 2 Aug 2026. Lead positioning with data
   sovereignty/self-hosting, not a high-risk compliance deadline.
   Source: https://www.lw.com/en/insights/eu-ai-act-gpai-model-obligations-in-force-and-final-gpai-code-of-practice-in-place

9. **SpiceDB consistency mode name and semantics: `at_least_as_fresh`.** It
   behaves like `minimize_latency` (default, ~5s enforced max staleness) unless the
   selected timestamp is older than the supplied ZedToken, in which case the chosen
   timestamp is adjusted forward to the ZedToken's timestamp. The other modes are
   `at_exact_snapshot` and `fully_consistent`. AuthZed recommends
   `at_least_as_fresh` over `fully_consistent`.
   Source: https://authzed.com/docs/spicedb/concepts/consistency

10. **SpiceDB ZedToken-bearing API names.** `WriteRelationships` returns a ZedToken
    in `written_at`; `DeleteRelationships` returns one in `deleted_at`;
    `CheckPermission` and `CheckBulkPermissions` also return ZedTokens. ZedToken is
    the Zanzibar zookie equivalent. (Note both `CheckBulkPermissions` and the older
    `BulkCheckPermission` naming appear in AuthZed material; use
    `CheckBulkPermissions`.)
    Source: https://authzed.com/docs/spicedb/concepts/consistency

11. **SpiceDB testing server and Testcontainers image.** `spicedb serve-testing`
    runs an isolated memdb datastore per unique preshared key on port 50051
    (read-only replica 50052). The official Testcontainers doc uses image
    `authzed/spicedb:v1.47.1` with `InsecureClient` over
    `get_endpoint()`/`get_secret_key()`.
    Sources: https://authzed.com/docs/spicedb/concepts/commands and
    https://authzed.com/docs/spicedb/integrations/testcontainers

12. **Contributor Covenant current version: 3.0 (released 2025-07-28).** Major
    rewrite, "Addressing and Repairing Harm" enforcement section. Version 2.1
    remains widely deployed. Pin the version and a real enforcement contact.
    Source: https://ethicalsource.dev/blog/contributor-covenant-3/

13. **GitHub PVR is public-repos-only and has no default notifications.** It is
    separate from SECURITY.md; enable both. Reporter is auto-credited on the draft
    advisory; maintainers must Watch > Custom > Security alerts to be notified.
    Source: https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/configuring-private-vulnerability-reporting-for-a-repository

14. **OpenSSF Best Practices "~10% pass": treat as a rough signal only.** A public
    snapshot showed 1019 passing of 5735 registered (about 18%); the share that
    achieve passing among all who start is lower. Use "hard but doable", not a
    precise statistic.
    Source: https://www.bestpractices.dev/en/criteria/0

15. **Apache NOTICE is optional for non-ASF pansoph.** NOTICE presence is mandated
    by ASF policy, not the license itself; only required when bundling a work that
    legally requires NOTICE attribution.
    Source: https://infra.apache.org/licensing-howto.html

---

## Open / uncertain

These could not be fully confirmed by the verifiers and should not be load-bearing
without further checking.

- **`authzed/action-spicedb` internals.** The action's `action.yml`/entrypoint
  were not directly inspected; that it runs `serve-testing` on memdb at port 50051
  (read-only 50052) is docs-attested, not source-verified. Confirm before relying
  on exact ports in CI config.
  https://github.com/authzed/action-spicedb

- **Onyx connector granularity.** Onyx's GitHub connector documents
  repository-level permission syncing specifically; the broader "document-level"
  framing is directionally correct but should be verified per connector.
  https://docs.onyx.app/admins/connectors/official/github

- **Digital Omnibus on AI is not yet fully formally adopted (as of 2026-06-25).**
  Council adoption / OJ publication of the Annex III deferral (2 Aug 2026 to 2 Dec
  2027) is expected July 2026 but not final; treat the deferral as expected, not
  enacted.
  https://www.lw.com/en/insights/eu-ai-act-gpai-model-obligations-in-force-and-final-gpai-code-of-practice-in-place

- **"Non-goals as a maturity signal" README convention.** This is a reasonable
  community convention rather than a single normative spec; the do-not-under-claim
  guidance is well supported, the specific "Non-goals section" practice is softer.
  https://www.makeareadme.com/

- **OpenSSF Best Practices precise current pass rate.** Only an older public
  snapshot (~18%) was available; the true current rate and the
  share-of-starters-who-pass were not pinned.
  https://www.bestpractices.dev/en/criteria/0

- **Glean ARR composition.** TechCrunch flags the $300M as partly consumption-based
  annualized run rate; the strictly-recurring portion was not disclosed.
  https://techcrunch.com/2026/05/28/gleans-top-line-crosses-300m-as-ai-budget-cutting-becomes-its-major-selling-point/

---

## Sources (verified 2026-06-23)

Launch readiness and OSS honesty:

- https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/configuring-private-vulnerability-reporting-for-a-repository
- https://baseline.openssf.org/versions/2025-10-10.html
- https://www.rfc-editor.org/info/rfc9116/
- https://www.bestpractices.dev/en/criteria/0
- https://ethicalsource.dev/blog/contributor-covenant-3/
- https://github.com/ossf/scorecard/blob/main/docs/checks.md
- https://adr.github.io/adr-templates/
- https://infra.apache.org/licensing-howto.html
- https://www.makeareadme.com/
- https://docs.onyx.app/admins/connectors/official/github
- https://github.com/truefoundry/cognita
- https://www.lw.com/en/insights/eu-ai-act-gpai-model-obligations-in-force-and-final-gpai-code-of-practice-in-place

Enforcement integration testing and SpiceDB consistency:

- https://authzed.com/docs/spicedb/concepts/consistency
- https://authzed.com/docs/spicedb/concepts/commands
- https://authzed.com/docs/spicedb/integrations/testcontainers
- https://www.docker.com/blog/rag-permission-testing-testcontainers-spicedb/
- https://www.pinecone.io/learn/rag-access-control/
- https://aws.amazon.com/blogs/security/authorizing-access-to-data-with-rag-implementations/
- https://www.cerbos.dev/features-benefits-and-use-cases/access-control-for-rag
- https://www.usenix.org/system/files/atc19-pang.pdf
- https://github.com/authzed/action-spicedb

Permission-layer repositioning and market:

- https://docs.onyx.app/security/architecture/access_controls
- https://github.com/onyx-dot-app/onyx
- https://github.com/truefoundry/cognita
- https://techcrunch.com/2026/05/28/gleans-top-line-crosses-300m-as-ai-budget-cutting-becomes-its-major-selling-point/
- https://www.lw.com/en/insights/eu-ai-act-gpai-model-obligations-in-force-and-final-gpai-code-of-practice-in-place
- https://www.cerbos.dev/features-benefits-and-use-cases/access-control-for-rag
- https://openfga.dev/docs/use-cases/rag-authorization
- https://github.com/ory/rerag-rebac
- https://github.com/pipeshub-ai/pipeshub-ai
- https://github.com/infiniflow/ragflow
