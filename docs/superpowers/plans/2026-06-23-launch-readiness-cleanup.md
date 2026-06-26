# Launch Readiness Cleanup (Workstream A)

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking. This is a
> one-day "make it honest and launchable" pass. It does NOT add features. It removes
> fiction from the README and docs, fixes dead links, and lands the day-one community
> health files. No em dashes anywhere. No co-author trailer. DCO sign-off (`-s`) on
> commits.

**Goal:** Make pansoph's public surface honest and launch-ready in a single focused day,
so the first impression matches the verified ground truth. The repo currently
under-claims in the README ("Nothing here ingests or retrieves yet") while real
retrieval, search, chat, and connector-management code exists, and it has dead ADR links
that 404 on GitHub. We fix both, and we land the community-health files a
security-positioned Apache-2.0 project is expected to ship.

**Working directory for all commands:** `/Users/yash/work/autoargos/pansoph`. Run
`export PATH="$HOME/.local/bin:$PATH"` first so `uv` is found.

**Non-goal:** This plan does NOT add live integration tests, does NOT wire durable
ingestion, and does NOT deepen any connector. Those are separate workstreams. Here we
only make the existing reality legible and honest.

---

## Ground truth this plan is built on (verified 2026-06-23)

State it plainly so the rewrites below do not over-claim or under-claim.

- **Works today (on in-memory fakes):** the retrieval enforcement logic (embed,
  hybrid_search with overfetch, `authz.bulk_check` at `at_least_as_fresh`, keep allowed
  subset, deny as a final filter, optional rerank) in
  `packages/pansoph-retrieval/src/pansoph_retrieval/pipeline.py:18-75`; the REST surface
  in `packages/pansoph-core/src/pansoph_core/routes.py:151-340` (`/api/search`,
  `/api/chat` SSE, `/api/grab`, `/api/health`, connector instance management); the
  SpiceDB gRPC adapter, the OpenFGA adapter, and the noop adapter; pgvector and Qdrant
  vector adapters. The full suite is 133 passed / 1 skipped, all on in-memory fakes
  (`FakeAuthz`, `FakeEmbedder`, `FakeVectorStore`). The permission red-team (27 tests) is
  CI-gated and validates enforcement LOGIC.
- **Unverified / in progress:** live-engine integration. `pytest -m integration` collects
  ZERO tests, and `connector-live-smoke.yml` runs `pytest -m integration -q || true`,
  which always passes. So no live SpiceDB / OpenFGA / pgvector behavior is exercised in
  CI yet. A SpiceDB schema bug, a ZedToken staleness bug, or a pgvector pre-filter group
  leak could ship CI-green.
- **Placeholder:** durable ingestion. `packages/pansoph-ingestion/.../worker.py:15` is a
  `print()` placeholder; scheduling is a best-effort in-process asyncio loop, not the
  durable engine.
- **Connectors:** 15 exist, but 10 of them had to set `incremental=False` after a
  data-loss fix; the advertised conformance engine ships with no connector
  `conformance.yaml`; only `InMemoryConnector` is wired into the ingest-to-retrieve test.
  ACL fidelity is weak exactly where ReBAC matters (Notion static ACL, Salesforce ignores
  org-wide-defaults and role hierarchy, SharePoint defers group expansion), and content
  extraction is demo-grade (Gmail snippet-only, Slack first history page only).

The rule for every rewrite below: describe what works on fakes as "works on fakes",
describe live integration as "unverified / in progress", and never imply a connector is
production-faithful when it is demo-grade.

---

## Task 1: Truth-in-README (replace the false status and quick-start framing)

The README says the project is a scaffold that ingests and retrieves nothing. That is
false and it wastes the first impression. Replace two blocks with honest, grounded text.

**File:** `README.md`

- [ ] **Step 1: Replace the `## Status` block.**

Replace the entire current Status section (`README.md:75-77`, the heading and its one
paragraph) with this exact text:

```markdown
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
```

- [ ] **Step 2: Fix the quick-start heading and its caveat.**

The `Quick start (once implemented)` heading at `README.md:64` is misleading in both
directions: the API and routes exist, but the one-command `docker compose` "demo" pulls
roughly 12 images and 16 services, which is far past a five-minute evaluation. Replace
the heading line:

```markdown
## Quick start
```

and immediately under the existing `docker compose ...` block, add this honest caveat:

```markdown
> Heads up: this profile brings up roughly a dozen services (Postgres, Valkey, an object
> store, SpiceDB, an identity provider, model servers, the API, and workers). It is a
> full self-hosted stack, not a five-minute taste. A single-command, single-connector
> demo behind a much smaller footprint is on the roadmap. Until then, see `DEMO.md` for
> the guided walkthrough.
```

- [ ] **Step 3 (verify):** `uv run ruff format --check .` is unaffected (Markdown is not
  formatted by ruff), but eyeball the rendered README. Acceptance: no occurrence of
  "Nothing here ingests or retrieves yet" remains, and "once implemented" is gone from
  the quick-start heading.

---

## Task 2: Fix the dead ADR links

The README and `CLAUDE.md` link the ADRs at `../architecture-decision-records/...`, a
sibling directory OUTSIDE the repo at
`/Users/yash/work/autoargos/architecture-decision-records`. Those relative links resolve
on the author's disk but **404 on GitHub**, because the directory is not in the repo.

ADRs belong in-repo, under version control, co-located with the code so links stay in
sync (Nygard convention, MADR). Three options:

- **Option A (recommended): copy the ADRs in-repo under `docs/adr/`.** Use the Nygard
  layout `docs/adr/NNNN-title.md`, keep the sequential numbers, and point the README and
  `CLAUDE.md` at relative in-repo paths. Simple, makes the links work on GitHub
  immediately, keeps history in this repo. The ADRs already exist as `README.md` files
  with `diagrams/` subdirectories, so this is a copy plus a path edit.
- **Option B: git submodule.** Add the `architecture-decision-records` repo as a
  submodule. Keeps a single source of truth if those ADRs are shared across projects, but
  submodules add clone friction and the GitHub web view still does not render submodule
  content inline. Heavier than the problem warrants for a solo project.
- **Option C: in-repo MADR copies (rewrite, not copy).** Re-author the two ADRs as fresh
  MADR 4.0 records under `docs/adr/`. Most work, best long-term format, but unnecessary
  today since the existing ADRs are already substantial.

**Recommendation: Option A.** Copy, then fix the links.

- [ ] **Step 1: Create the in-repo ADR directory and copy both ADRs.**

```bash
mkdir -p docs/adr
cp -R /Users/yash/work/autoargos/architecture-decision-records/adr-0001-modular-knowledge-platform docs/adr/0001-modular-knowledge-platform
cp -R /Users/yash/work/autoargos/architecture-decision-records/adr-0002-finalized-tech-stack docs/adr/0002-finalized-tech-stack
```

(Keep each ADR's `README.md` and `diagrams/` as-is.)

- [ ] **Step 2: Fix the README ADR links.** In `README.md`, replace the two ADR links
  (currently `README.md:61-62`):

```markdown
- ADR-0001 the product vision: `../architecture-decision-records/adr-0001-modular-knowledge-platform/`
- ADR-0002 the finalized tech stack: `../architecture-decision-records/adr-0002-finalized-tech-stack/`
```

with:

```markdown
- ADR-0001 the product vision: [`docs/adr/0001-modular-knowledge-platform/`](docs/adr/0001-modular-knowledge-platform/README.md)
- ADR-0002 the finalized tech stack: [`docs/adr/0002-finalized-tech-stack/`](docs/adr/0002-finalized-tech-stack/README.md)
```

- [ ] **Step 3: Fix the `CLAUDE.md` ADR links.** Update the three references in
  `CLAUDE.md` (section 4 line 45, section 12 lines 178-179, and the connector section)
  from `../architecture-decision-records/adr-000X-.../` to the matching in-repo
  `docs/adr/000X-.../` paths.

- [ ] **Step 4 (verify):** `grep -rn "architecture-decision-records" README.md CLAUDE.md`
  returns nothing. Acceptance: no link in any tracked file points outside the repo.

---

## Task 3: Fix the `app.py` docstring

`packages/pansoph-core/src/pansoph_core/app.py:1` still calls the chat stream a
"placeholder SSE chat stream", which is stale: the routes are wired and the SSE endpoint
follows the AI SDK UI Message Stream protocol.

- [ ] **Step 1:** In `packages/pansoph-core/src/pansoph_core/app.py`, change the first
  docstring line from:

```python
"""The FastAPI application: REST, health, and a placeholder SSE chat stream.
```

to:

```python
"""The FastAPI application: REST, health, and the SSE chat stream.
```

- [ ] **Step 2 (verify):** `uv run ruff check packages/pansoph-core` passes and `grep -n
  placeholder packages/pansoph-core/src/pansoph_core/app.py` returns nothing on line 1.
  (The lifespan docstring's "Best effort" note is accurate and stays.)

---

## Task 4: Community-health files

A security-positioned, Apache-2.0, self-hosted project is expected to ship a standard set
of community-health files. Two of them (`SECURITY.md`, `CONTRIBUTING.md`) are landing
alongside this plan. The table marks what is day-one versus later.

| File | Day-one? | Notes |
|---|---|---|
| `SECURITY.md` | **Day one** | CVD policy, private reporting, scope (enforcement path + connector ACLs highest), safe harbor, SLAs. Landing with this plan. |
| `CONTRIBUTING.md` | **Day one** | Setup, gates, ports-and-adapters model, DCO sign-off, connector docs-first rule. Landing with this plan. |
| `CODE_OF_CONDUCT.md` | **Day one** | Drop in Contributor Covenant. Pin a version and a real enforcement contact email. See Step 1. |
| `.github/ISSUE_TEMPLATE/` | **Day one** | Bug report and feature request templates (Markdown or YAML form). Low effort, sets expectations. |
| `.github/PULL_REQUEST_TEMPLATE.md` | **Day one** | Checklist: gates pass, permission/conformance test added if enforcement/connector touched, DCO signed. |
| `CODEOWNERS` | **Day one** | Single owner for now (`* @<owner>`); makes review routing explicit. |
| `NOTICE` | **Later (optional)** | Apache-2.0 does NOT require a `NOTICE` for a non-ASF project. Only needed if you bundle a dependency that ships its own `NOTICE`. A clean `LICENSE` plus per-file headers suffice. Add only when a bundled dep demands it. |
| `CHANGELOG.md` | **Later** | Start at the first tagged release (Keep a Changelog format). Nothing to log yet. |

- [ ] **Step 1: Add `CODE_OF_CONDUCT.md`.** Use Contributor Covenant. Pin a specific
  version: Contributor Covenant 3.0 (released 2025-07-28, restorative-justice enforcement
  framing) or the still-widely-deployed 2.1. Either is a drop-in. Fill the enforcement
  contact with the same real email used in `SECURITY.md`. Do not leave the
  `[INSERT CONTACT METHOD]` placeholder.

- [ ] **Step 2: Add `.github/ISSUE_TEMPLATE/bug_report.md` and `feature_request.md`** (or
  YAML issue forms). Bug report asks for: version/commit, adapter config (authz, vector),
  reproduction, expected vs actual. Add a `config.yml` that links security reports to
  `SECURITY.md` (do not let security bugs land as public issues).

- [ ] **Step 3: Add `.github/PULL_REQUEST_TEMPLATE.md`** with a checklist: ran the three
  gates, added a permission test if enforcement changed, added a conformance run if a
  connector changed, DCO signed off (`-s`), no em dashes, no co-author trailer.

- [ ] **Step 4: Add `CODEOWNERS`** at repo root or `.github/CODEOWNERS` with `* @<owner>`.

- [ ] **Step 5 (verify):** GitHub's community profile (Insights, Community Standards)
  shows green for Description, README, Code of conduct, Contributing, License, Security
  policy, Issue templates, and Pull request template.

---

## Task 5: OpenSSF Scorecard / Best Practices Badge target table

Set realistic targets. A solo repo can pass roughly 8 to 10 Scorecard checks on day one;
the rest are gated on project history (a second reviewer, time, a signed release).

### OpenSSF Scorecard, day-one expectation

| Check | Day-one status | Why |
|---|---|---|
| License | Pass | Clean Apache-2.0 `LICENSE` present. |
| Security-Policy | Pass | `SECURITY.md` lands with this plan. |
| Binary-Artifacts | Pass | No committed binaries. |
| Dangerous-Workflow | Pass | Workflows use no untrusted `pull_request_target` script injection. |
| Token-Permissions | Pass after a small fix | Add a top-level `permissions: contents: read` to each workflow. |
| SAST | Pass after enabling | Enable CodeQL (or keep `ruff`/`mypy` and document). One workflow file. |
| Pinned-Dependencies | Partial to pass | Pin GitHub Actions by commit SHA (currently pinned by tag, e.g. `@v4`, `@v5`). |
| Dependency-Update-Tool | Pass after enabling | Add a `dependabot.yml` (or Renovate) for `uv`/pip and Actions. |
| CI-Tests | Pass | `ci.yml` already runs lint, type, and test on every PR. |
| Vulnerabilities | Pass if clean | Depends on the current dependency set; run `osv-scanner` to confirm. |
| Code-Review | **Not yet** | Needs a second reviewer or enforced review history. |
| Contributors | **Not yet** | Needs contributors from 2+ orgs over time. |
| Maintained | **Not yet** | Accrues from sustained commit/issue activity. |
| Signed-Releases | Partial | `release.yml` already does keyless cosign signing; this passes once a signed release is tagged. |
| Branch-Protection | Depends | Needs branch-protection settings on `main` and an admin token for Scorecard to read them. |
| CII-Best-Practices | **Not yet** | Earned by completing the Best Practices badge below. |

Day-one realistic target: pass License, Security-Policy, Binary-Artifacts,
Dangerous-Workflow, Token-Permissions, SAST, Pinned-Dependencies,
Dependency-Update-Tool, CI-Tests, Vulnerabilities. That is the 8-to-10-check band.

### OpenSSF Best Practices Badge (self-certified, free, at bestpractices.dev)

Achievable solo but deliberate. To reach "passing" all MUST/MUST-NOT criteria must be
met and all SHOULD criteria met or justified. Several are already satisfied by what this
plan lands: a project description and how-to-contribute (README + `CONTRIBUTING.md`), a
vulnerability-report process (`SECURITY.md`), an OSI license (Apache-2.0), version
control and a public issue tracker (GitHub), and an automated test suite in CI. The
badge explicitly requires the project site to describe what the software does and how to
obtain, give feedback on, and contribute to it, which the README and `CONTRIBUTING.md`
now do. Register the project after this cleanup and fill the questionnaire.

- [ ] **Step 1 (optional, recommended):** Add a top-level `permissions: contents: read`
  to each workflow and pin Actions by commit SHA. These two edits move Token-Permissions
  and Pinned-Dependencies to pass.
- [ ] **Step 2 (optional):** Add `.github/dependabot.yml` for `github-actions` and the
  Python ecosystem.
- [ ] **Step 3 (later):** Register at https://www.bestpractices.dev and complete the
  passing questionnaire once the day-one files are merged.

---

## Acceptance criteria

This cleanup is done when all of the following hold:

- [ ] `grep -rn "Nothing here ingests or retrieves yet" README.md` returns nothing, and
  the README has a "What works today" / "What is in progress" split grounded in the
  ground truth above (no overclaim, no underclaim).
- [ ] `grep -rn "once implemented" README.md` returns nothing.
- [ ] `grep -rn "architecture-decision-records" README.md CLAUDE.md` returns nothing; the
  ADRs are reachable in-repo under `docs/adr/` and the README links resolve on GitHub.
- [ ] `app.py:1` no longer says "placeholder SSE chat stream".
- [ ] `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (versioned, real contact),
  `.github/ISSUE_TEMPLATE/`, `.github/PULL_REQUEST_TEMPLATE.md`, and `CODEOWNERS` exist.
- [ ] GitHub Community Standards shows green for every standard item.
- [ ] `uv run ruff check . && uv run ruff format --check . && uv run pytest -q` still
  passes (this plan changes docs and one docstring, so the suite must remain green).
- [ ] No em dashes were introduced in any new or edited file. No co-author trailer in any
  commit. Commits are conventional and DCO-signed (`-s`).

## Definition of done

A newcomer landing on the GitHub repo reads a README that accurately states what works
(enforcement logic on fakes), what does not yet (live integration, durable ingestion,
connector depth), and what pansoph is for. Every link resolves. The security,
contributing, and conduct expectations are documented. The community profile is green.
Nothing in the public surface claims a capability the test suite does not back, and
nothing under-sells the real, tested enforcement path. The change set is docs and one
docstring only, so CI stays green.

## Sources (verified 2026-06-23)

- OpenSSF Scorecard checks (day-one-passable vs history-gated set):
  https://github.com/ossf/scorecard/blob/main/docs/checks.md
- OpenSSF Best Practices criteria (self-certified, free; MUST/SHOULD rules; site must
  describe and explain contribution): https://www.bestpractices.dev/en/criteria/0
- OpenSSF OSPS Baseline 2025-10-10 (CVD policy with a response timeframe is a MUST):
  https://baseline.openssf.org/versions/2025-10-10.html
- Contributor Covenant 3.0 (released 2025-07-28):
  https://ethicalsource.dev/blog/contributor-covenant-3/
- ADR conventions, in-repo `doc/adr/NNNN-title.md`, MADR 4.0:
  https://adr.github.io/adr-templates/
- Apache-2.0 `NOTICE` is ASF policy, not a license requirement, optional for a non-ASF
  project: https://infra.apache.org/licensing-howto.html
- GitHub Docs, private vulnerability reporting:
  https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/configuring-private-vulnerability-reporting-for-a-repository
- README best practice (be specific, do not under-claim):
  https://www.makeareadme.com/
