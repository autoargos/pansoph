# Plan: test every connector and the full platform end to end

Date: 2026-06-26
Status: proposed
Owner: maintainer decision required on several items (see Open Questions)
License context: Apache 2.0, model agnostic, self host first. Nothing here adds a hardcoded vendor or a hosted dependency.

This plan answers one question the maintainer asked: how do we actually test out each connector, and the whole platform, end to end. It is written against the repository as it stands on 2026-06-26. The "Current state" section is honest about what exists and what is fiction. Everything past that is marked proposed and broken into independently shippable phases with checkboxes and acceptance criteria.

The single thing that matters most for pansoph is the headline guarantee: a user can only ever retrieve what they are already allowed to see, across every connected source. So the security tier (ACL fidelity per connector) and the full stack permission E2E are the highest priority items in this plan, above feature breadth.

---

## A. Current state (no fiction)

### A.1 What exists today

- Sixteen connector directories under `connectors/`: confluence, dropbox, github, gmail, google_drive, granola (deferred/skipped), jira, linear, notion, salesforce, sharepoint, slack, teams, weblink, zendesk.
- Each connector ships exactly one bespoke hermetic test, `connectors/<x>/tests/test_<x>_conformance.py`, driven by recorded HTTP through `respx`. They call the category checks directly (`check_discovery`, `check_spec`, `check_permissions`) rather than the engine. See `connectors/github/tests/test_github_conformance.py` for the canonical shape.
- A conformance engine exists in `packages/pansoph-connector-sdk/src/pansoph_connector_sdk/verify/engine.py` (`run_conformance`), with a config parser (`verify/config.py`), and categories `contract.py`, `read.py`, `sync.py`, `permissions.py`. `assert_conforms` in `conformance.py` is also exported.
- The permission category (`verify/categories/permissions.py`) is strong: it asserts set equality on the produced `AccessControl` and per principal visibility equivalence, and treats oversharing (a superset) as a hard failure. This is the right shape for ACL fidelity, but today it only runs against recorded fixtures.
- The platform enforcement path is live tested: `tests/integration/test_enforcement_live.py` (8 tests) runs the real `retrieve()` against a real SpiceDB and a real pgvector via `deploy/compose.test.yaml`, gated by the new `enforcement-live` CI job. This proves the enforcement path, not connectors and not the agora API.
- The in memory vertical `tests/test_ingest_retrieve.py` runs the real ingestion pipeline plus the real ACL to tuple mappers plus the real retrieval path, with fakes for embedder, vector store, and authz. Only `InMemoryConnector` is wired through ingest to retrieve.
- The agora API surface exists in `packages/pansoph-core/src/pansoph_core/routes.py`: `POST /api/search`, `POST /api/chat` (SSE via `EventSourceResponse`), `POST /api/grab`, `GET /api/health`, connector instance management.
- A permission segmented demo exists: `DEMO.md`, `docs/demo-queries.md`, `deploy/seed-demo.sh` (Northwind departments backed by the sample MCP server, plus a public GitHub repo, signed in as alice/bob/carol/dave through Keycloak).

### A.2 Honest gaps (the fiction to retire)

1. **The conformance engine is dead code for the real connectors.** Zero connectors ship a `conformance.yaml` (`find connectors -name conformance.yaml` returns 0). So `run_conformance` and its config driven category gating are never exercised by any real connector. The connectors pass through bespoke per file tests instead. CLAUDE.md section 9 says "every connector passes the one shared conformance suite (`assert_conforms`)"; that is currently aspirational for the wave.
2. **`check_deletions` does not exist.** There is no deletions check anywhere in `pansoph-connector-sdk`. `verify/config.py` defines a `DeletionsCategory`, and `sync.py`'s docstring mentions deletions, but no function tests them. Yet confluence, dropbox, github, google_drive, sharepoint, and zendesk all declare `deletions=True`. Those are advertised but unverified capabilities, which under section 6 of the engineering guide is a security relevant defect (a `deletions=True` that does not propagate a tombstone silently leaves a revoked or deleted document retrievable).
3. **The live smoke job is a no op.** `.github/workflows/connector-live-smoke.yml` runs `uv run pytest -m integration -q || true` nightly. With `|| true` it always passes; with no connector marked for live integration it collects nothing; and `-m integration` now also matches the platform enforcement suite, not connector live smoke. So the nightly "live smoke" proves nothing about any connector against any real source. It also names `GITHUB_SMOKE_TOKEN` but no test reads it.
4. **Incremental was disabled across the wave after a data loss fix.** Only confluence, github, google_drive declare `incremental=True`. Everything else is `incremental=False` (full sync) after commit `ccf56bf`. That is the safe choice, but it means most connectors re fetch everything every run, and the few that claim incremental need the abnormal state assertion to prove they actually honor the cursor.
5. **ACL fidelity is weakest exactly where ReBAC matters.** Notion is a single static operator configured ACL (it declares `permission_sync=False`, which is honest); Salesforce ignores org wide defaults and the role hierarchy; SharePoint defers group membership expansion. Content extraction is demo grade: Gmail snippet only, Slack first `conversations.history` page only, SharePoint `text=None`. These are real correctness limits, not cosmetic.
6. **No real connector is exercised through ingest to retrieve.** The only end to end vertical uses `InMemoryConnector`.
7. **The agora API stack has no E2E.** Nothing drives `POST /api/search` or the `POST /api/chat` SSE stream as a real identity through the full FastAPI app.
8. **The `/api/chat` SSE shape is bespoke, not the AI SDK UI Message Stream.** `routes.py` emits `{"type": "source"|"text-delta"|"finish"}` events, not the `text-start`/`text-delta`/`text-end` parts and `x-vercel-ai-ui-message-stream: v1` header that ADR-0002 and lens (assistant-ui plus AI Elements) target. Any SSE E2E must assert the shape the code actually emits today, and a separate decision is whether to converge on the AI SDK protocol.
9. **Durable ingestion is unproven.** `packages/pansoph-ingestion/src/pansoph_ingestion/worker.py` is a `print()` placeholder (workflow lands with task #16); scheduling is an in process asyncio loop. Crash/resume and idempotent re sync cannot be tested until the DBOS workflow is wired.

### A.3 Per connector readiness table

Built from the in repo inventory on 2026-06-26. "Hermetic test" means a `test_<x>_conformance.py` exists and runs offline through respx. "conformance.yaml" is the declarative engine config (none ship). "Deletions declared vs verified" reflects the capability flag against the fact that no `check_deletions` exists. ACL fidelity and content fidelity are graded from the connector docstrings and known limits. Tier is the proposed starting tier (see section B).

| Connector | Hermetic test | conformance.yaml | Incremental | Deletions declared vs verified | ACL fidelity | Content fidelity | Proposed tier |
|---|---|---|---|---|---|---|---|
| confluence  | yes | none | declared true | true declared, unverified (no check) | permission_sync, real space/page ACL | reasonable | T1 + T3-ACL + T4 |
| dropbox     | yes | none | false (full) | true declared, unverified | permission_sync, shared folder ACL | reasonable | T1 + T3-ACL + T4 |
| github      | yes | none | declared true | true declared, unverified | permission_sync, collaborators + teams | good | T1 + T3-ACL + T4 (first live) |
| gmail       | yes | none | false (full) | n/a (deletions false) | permission_sync, mailbox owner | weak (snippet only) | T1 + T3-ACL |
| google_drive| yes | none | declared true | true declared, unverified | permission_sync, file/drive ACL | reasonable | T1 + T3-ACL + T4 |
| granola     | deferred/skipped | none | n/a | n/a | n/a | n/a | deferred |
| jira        | yes | none | false (full) | n/a (deletions false) | permission_sync, project ACL | reasonable | T1 + T3-ACL |
| linear      | yes | none | false (full) | n/a (deletions false) | permission_sync, team ACL | reasonable | T1 + T3-ACL |
| notion      | yes | none | false (full) | n/a (deletions false) | weak: single static operator ACL, no per page (permission_sync=false, honest) | reasonable | T1 only until per page ACL |
| salesforce  | yes | none | false (full) | n/a (deletions false) | weak: ignores org wide defaults + role hierarchy | reasonable | T1 + T3-ACL (gap fix first) |
| sharepoint  | yes | none | false (full) | true declared, unverified | weak: defers group membership expansion | weak (text=None) | T1 + T3-ACL (gap fix first) |
| slack       | yes | none | false (full) | n/a (deletions false) | permission_sync, channel members | weak (first history page only) | T1 + T3-ACL |
| teams       | yes | none | false (full) | n/a (deletions false) | permission_sync, team/channel ACL | reasonable | T1 + T3-ACL |
| weblink     | yes | none | false (full) | n/a | public link, consent captured | reasonable | T1 |
| zendesk     | yes | none | false (full) | true declared, unverified | permission_sync, group/org ACL | reasonable | T1 + T3-ACL + T4 |

Three headline findings from the table, restated plainly:
- The conformance engine is shipped but unused (no `conformance.yaml` anywhere).
- `deletions=True` is declared by six connectors and verified by none, because `check_deletions` does not exist.
- `connector-live-smoke.yml` is a `|| true` no op that collects nothing.

---

## B. The tiered connector testing model

Mature connector projects (Airbyte, Singer/Meltano, Onyx) converge on a testing pyramid because no single tier catches all three failure classes: connector LOGIC bugs, CONTRACT/conformance violations, and upstream API DRIFT. pansoph adds a fourth, security critical tier for a ReBAC platform: ACL fidelity. We adopt four tiers.

**Governing rule (non negotiable, enforced in CI):** a connector may only DECLARE a capability (`incremental`, `deletions`, `permission_sync`) if a test in the matching tier actually verifies it. A declaration without a matching passing test is a build failure, not a TODO. This directly implements engineering guide section 6 ("a connector built from stale assumptions is worse than no connector") and section 3 rule 6 ("no fiction").

### Tier 1: hermetic (recorded cassettes / respx). Per PR. Already exists.

This is the bulk of the tests and it works today. What to standardize:

- [ ] Keep `respx` for fine grained mock assertions where the test needs explicit control over each call (the github test is the model). Where a connector makes many sequential calls, allow VCR style recorded cassettes via `pytest-recording` so a single recorded flow replays offline. Both are acceptable; pick per connector by call complexity.
- [ ] Block the network in the conformance job (`pytest --block-network` once `pytest-recording` is a dev dependency) so a hermetic test that accidentally reaches the network fails loudly instead of silently hitting a live API.
- [ ] Redact secrets at record time (`filter_headers`, `filter_query_parameters`) so no token ever lands in a committed cassette. Add a CI check that greps committed fixtures for token shaped strings.
- [ ] Standardize the fixture layout: `connectors/<x>/tests/fixtures/<category>/` (the github test already uses `fixtures/permissions/`). Tier 1 owns logic: pagination, schema parsing, error/retry handling, and cursor handling.

Tier 1 catches logic regressions. By construction it cannot see that the live API changed (the cassette is frozen). That is Tier 4's job.

### Tier 2: declarative conformance. Per PR. DECISION REQUIRED.

There are two coherent end states and the maintainer must pick one. Do not let the engine sit half alive.

**Option 2A: wire the existing engine.** Ship a `conformance.yaml` per connector and run them through `run_conformance` (or `assert_conforms`), and implement the missing `check_deletions`.
- Work: write one `conformance.yaml` per connector (the parser in `verify/config.py` already supports `spec`, `connection`, `discovery`, `read`, `incremental`, `deletions`, `permissions`); implement `check_deletions` in `verify/categories/sync.py` (create -> sync -> delete -> sync -> assert a tombstone/deletion record with the PK propagates); make the engine assert that any declared capability has its category present, and that a present capability category actually ran (no silent skip). Add a CI assertion that every connector ships a `conformance.yaml`.
- Tradeoffs: uniform contract coverage across all connectors, one place to add a new universal check, machine readable capability declarations. Cost: every connector now carries a YAML plus fixtures, and the engine becomes load bearing so its bugs gate every connector. This is the Airbyte CAT model and it scales to a community connector ecosystem.

**Option 2B: formally retire the engine, keep bespoke tests.** Delete `verify/engine.py`, `verify/config.py`, and the `run_conformance`/`ConformanceResult` exports; keep the category functions (`check_spec`, `check_discovery`, `check_permissions`, `check_incremental`, and a new `check_deletions`) as a library the bespoke per connector tests import directly (which is already how they are used).
- Tradeoffs: less indirection, the tests read straight through, no dead YAML. Cost: no machine enforced uniformity (a connector can quietly skip a category), and CLAUDE.md section 9's "one shared conformance suite" claim must be rewritten to "a shared category library." Worse fit for a large community connector ecosystem.

**Recommendation (maintainer decides, not assumed):** lean Option 2A if connector breadth and outside contributors are near term goals, because declarative conformance is what makes a hundred community connectors verifiable without reading each test; lean Option 2B if the connector set stays small and maintainer owned, because the bespoke tests are already clearer than the engine. Either way, `check_deletions` must be implemented and every `deletions=True` connector must pass it, or those connectors must drop the flag to `deletions=False`. That part is not optional under the governing rule, independent of 2A vs 2B.

### Tier 3: ACL fidelity (the SECURITY tier). Per PR against fixtures + nightly against live. HIGHEST PRIORITY.

This is the most important tier for pansoph and it gets first claim on effort. The existing `check_permissions` is the right engine for the fixture half; extend it into a per connector security suite that mirrors the source permission model faithfully (the Onyx Auto Sync Permissions model: a user sees only what they could access natively).

For each `permission_sync=True` connector, assert per principal visibility equivalence with four cases:
- [ ] Positive: principal A retrieves exactly A's source visible documents.
- [ ] Negative oversharing red team: principal B must NOT retrieve a doc only A can access. `check_permissions` already fails a superset, so extend the existing oversharing regression pattern with at least one explicit B cannot see A's doc assertion per connector.
- [ ] Group membership: an external group to ACL mapping resolves correctly (this is exactly where SharePoint defers expansion and Salesforce drops the role hierarchy today, so those connectors get a failing test that documents the gap until fixed).
- [ ] Revocation: remove access in the source fixture, re sync, assert the doc disappears for that principal.

Treat an ACL fidelity failure as a release blocker, not a flaky test. Real bugs here are common and subtle (Onyx's Slack perm sync ignored the connector start date and iterated 33K messages, issue #9664). Wire this suite under `@pytest.mark.permission` so it joins the existing release gating `permission-gate` job.

### Tier 4: live smoke against real test tenants. Nightly, non blocking, drift detecting. Replaces the no op.

This is the only tier that detects upstream API drift, and it is the fix for `connector-live-smoke.yml`.

- [ ] Replace `pytest -m integration -q || true` with a real, scheduled, non blocking job that runs `-m connector_live` (a NEW marker distinct from `integration`, so it does not collide with the platform enforcement suite) and reports drift through a PR, never auto merging.
- [ ] Per provider sandbox/token acquisition (feasibility varies sharply; plan the live tier around what each vendor actually offers free):
  - Salesforce Developer Edition (free, persistent).
  - GitHub test org + fine grained PAT scoped to that org (free). This is the first live target.
  - Notion internal integration test workspace (free).
  - Slack Enterprise Grid dev sandbox via the Developer Program (free, 3 day message retention, needs SSO/Simple IdP).
  - Zendesk sandbox (Enterprise plan only; deactivates webhooks, rewrites emails to @example.com).
  - Microsoft 365 dev tenant (now gated behind a qualifying subscription; sharepoint/teams live deferred until a tenant exists).
  - Atlassian sandbox (needs Premium/Enterprise; confluence/jira live deferred or run against a 30 day trial).
  - Google Workspace test domain (needed for google_drive/gmail live).
  - Dropbox/Linear: dedicated free test accounts.
- [ ] Credential isolation in CI: GitHub OIDC short lived tokens where the provider supports it; scoped write only environment secrets gated by environment protection rules with required reviewers otherwise; pin third party actions to a full commit SHA; deny by default egress allowlist on the live job.
- [ ] Drift detection that diffs, not blindly overwrites: re record cassettes in `record_mode=all` against the sandbox, diff the new recordings against the committed golden cassettes (and the OpenAPI spec via oasdiff where one exists) with strict method+path+body matching, and open a PR only on a real diff. Two no op failure modes to avoid: loose matching that hides schema changes, and auto committing the new cassette over the golden one without a diff gate.

---

## C. Full platform end to end

The 2026 consensus for E2E testing a self hosted RAG/enterprise search platform: stand up the REAL stack with disposable real services, drive the PUBLIC API boundary, assert end to end, with special attention to streaming, permission/tenant isolation, and durable ingestion recovery.

### C.1 The compose based E2E harness

- [ ] Extend `deploy/compose.test.yaml` or add a new `deploy/compose.e2e.yaml` that brings up: agora (`deploy/Dockerfile.agora`) + scribe (`deploy/Dockerfile.scribe`) + the existing SpiceDB + pgvector, plus a STUB embedder/LLM (a tiny OpenAI compatible endpoint returning deterministic vectors and a fixed completion, so the test is model agnostic and deterministic and adds no heavy model dependency). Reuse the real compose file shape so dev and CI do not drift.
- [ ] Bring the stack up with the Testcontainers Compose module (`ComposeContainer`, Compose v2) so the harness gets programmatic host/port discovery and per service listening port wait strategies (the wait strategy kills the number one E2E flake source: starting tests before services are ready). Run on native Docker CI runners; avoid Docker in Docker.
- [ ] Add explicit `if: always()` teardown plus a job timeout so a failed run never leaks containers or volumes.
- [ ] Seed the permission segmented demo corpus before any test runs, reusing `deploy/seed-demo.sh` and the Northwind/Keycloak personas (alice, bob, carol, dave). Keep it deterministic.

### C.2 The headline E2E assertion (promote the red team to full stack)

Drive the real `POST /api/search` as alice and as bob through the running agora, at the HTTP boundary, and assert:
- [ ] Alice (engineering) retrieves engineering and public docs; Bob (not engineering) retrieves only public and his own. Alice cannot retrieve Bob's restricted document and vice versa. This promotes `tests/test_ingest_retrieve.py` (in memory fakes) and `tests/integration/test_enforcement_live.py` (enforcement path only) to the full agora API stack.
- [ ] Revocation propagates on the next request: delete the relationship (re sync the instance with a contracted ACL, or delete the SpiceDB tuple), re query as the same principal, assert the document is gone. This is the Zanzibar new enemy case asserted at the API boundary, with the `at_least_as_fresh` ZedToken threaded by `retrieve()`.
- [ ] Tenant isolation: a query scoped to tenant t2 never surfaces a t1 document.
- Any single rule failure fails the whole suite. Mark it `@pytest.mark.permission` so it is release gating.

### C.3 SSE assertion approach for /api/chat

Test the streaming endpoint by POSTing with `Accept: text/event-stream`, then parsing the SSE stream chunk by chunk (buffer incomplete lines, split on newlines, strip the `data: ` prefix, stop at the terminal event).

- [ ] Assert the shape the code emits TODAY (honest): an ordered sequence of `{"type": "source", ...}` events (one per cited hit, with `doc_id`/`chunk_id`), then `{"type": "text-delta", "delta": ...}` events whose concatenated deltas equal the model answer, then a single `{"type": "finish"}`. Assert `content-type: text/event-stream`.
- [ ] Assert per request identity is reflected in the stream content: chat as alice cites only alice visible sources; chat as bob never cites a restricted doc (the source events ARE the citation set, so this is a streaming oversharing check).
- [ ] Separate, gated on a maintainer decision (see Open Questions): if pansoph converges on the Vercel AI SDK UI Message Stream protocol that ADR-0002 and lens target, upgrade the assertions to that shape (header `x-vercel-ai-ui-message-stream: v1`; ordered `text-start` -> `text-delta` -> `text-end` with matching ids; `source-url`/`source-document` citation parts; `start-step`/`finish-step`; terminal `[DONE]`; error parts and abort handling). Do not assert this shape until the code emits it.

### C.4 Demo as E2E (so the demo cannot rot)

- [ ] Promote the `DEMO.md` / `docs/demo-queries.md` walkthrough into the same harness as an always green executable smoke test: run `deploy/seed-demo.sh` against the composed stack, then replay the documented alice/bob curl queries and assert the documented results. Version control it alongside the code so the demo evolves with it. Keep it read only and non flaky against the production like compose env. A demo wired into CI cannot silently rot.

### C.5 Durable ingestion E2E (BLOCKED, specified now)

This is blocked until the durable worker is wired (`pansoph-ingestion/worker.py` is a `print()` placeholder; the DBOS workflow lands with task #16). Specify the tests now so they land with the worker:
- [ ] Crash injection / resume from checkpoint: start an ingest, kill the scribe worker mid run at a persistence boundary, restart, assert the run resumes from the durable cursor (a checkpoint) rather than restarting, and the corpus is indexed exactly once.
- [ ] Idempotent re sync (exactly once oracle): run the same sync twice; assert the vector store shows each chunk once and the second run is a no op against an idempotency key (new key -> process and store; duplicate -> return stored result, keys TTL'd). Steps touching external systems are at least once, so every such step must be idempotent and the duplicate path must be tested.
- [ ] Replay/determinism: once histories exist, record a real workflow history and replay it in CI to catch non determinism (time/rand/reordered or removed steps) before it silently breaks an in flight workflow.
- [ ] Mark all of the above `@pytest.mark.skip(reason="blocked on durable worker, task #16")` until the worker lands, so the intent is in the tree and visible, never a silent gap.

---

## D. CI wiring

Where each tier runs, how it relates to the new `enforcement-live` job, and the fix for `connector-live-smoke.yml`.

| Tier / suite | Marker | Where it runs | Gating |
|---|---|---|---|
| Tier 1 hermetic | `conformance` | `conformance-gate` (existing, per PR, offline, `--block-network`) | blocks merge |
| Tier 2 declarative conformance | `conformance` | same `conformance-gate` (if Option 2A) | blocks merge |
| Tier 3 ACL fidelity (fixture half) | `permission` | `permission-gate` (existing, per PR) | release gate |
| Platform permission E2E (C.2) + demo (C.4) + SSE (C.3) | `permission` + `e2e` | NEW `e2e` job (compose, per PR or nightly per maintainer) | release gate for the permission assertions |
| Platform enforcement path | `integration` | `enforcement-live` (existing, real SpiceDB + pgvector) | blocks merge |
| Tier 4 connector live smoke + Tier 3 live half | `connector_live` | `connector-live-smoke.yml` (nightly, non blocking, drift PR) | never gates merge |

Relationship to `enforcement-live`: it already proves the enforcement path against real engines on `-m integration`. Introduce a distinct `connector_live` marker so the nightly connector smoke does not collide with or get masked by the platform integration suite. The new full stack `e2e` job is the layer above `enforcement-live`: enforcement-live tests `retrieve()`, the e2e job tests the agora HTTP/SSE boundary.

Fix for `connector-live-smoke.yml`:
- [ ] Remove `|| true`. The job stays non blocking by living on `schedule` (nightly) and `workflow_dispatch`, not by swallowing the exit code. A scheduled job that fails should show red so the maintainer sees drift.
- [ ] Change `-m integration` to `-m connector_live` so it collects real connector live tests, not the platform enforcement suite.
- [ ] Gate the whole job on credentials being present (skip cleanly with a clear message if no sandbox secrets are configured, rather than passing vacuously).
- [ ] Wire the drift PR (`peter-evans/create-pull-request`, SHA pinned) so a real cassette/schema diff opens a review PR instead of just printing `git status`.

---

## E. Sequencing, acceptance criteria, open questions

Each phase is independently shippable and leaves the tree green.

### Phase 0: stop the fiction (small, do first)
- [ ] Decide and apply the capability truth fix: either implement `check_deletions` and make the six `deletions=True` connectors pass it, OR drop those flags to `deletions=False` until a test exists. No `deletions=True` may survive without a passing deletions test.
- [ ] Fix `connector-live-smoke.yml` per section D (remove `|| true`, switch marker, gate on creds). Even before live tests exist, it must not pass vacuously.
- Acceptance: no connector declares a capability that lacks a verifying test; the live smoke job no longer passes by swallowing its exit code.

### Phase 1: Tier 3 ACL fidelity (highest priority)
- [ ] Extend `check_permissions` usage into a per connector security suite with the four cases (positive, oversharing negative, group membership, revocation) for every `permission_sync=True` connector, under `@pytest.mark.permission`.
- [ ] Write failing tests that document the known weak spots (Salesforce role hierarchy / org wide defaults, SharePoint group expansion) so the gap is tracked, not hidden.
- Acceptance: `permission-gate` runs an ACL fidelity case per connector; an injected oversharing bug fails the gate.

### Phase 2: Tier 2 conformance decision and Tier 1 standardization
- [ ] Maintainer picks Option 2A (wire engine, ship `conformance.yaml` per connector) or 2B (retire engine, keep category library); apply it; update CLAUDE.md section 9 to match reality.
- [ ] Standardize Tier 1: add `pytest-recording`, `--block-network` in the conformance job, secret redaction check.
- Acceptance: either every connector ships a `conformance.yaml` and runs through the engine, or the dead engine is deleted and the bespoke tests import a documented category library; the conformance job blocks the network.

### Phase 3: full platform E2E harness (C.1 to C.4)
- [ ] Add `deploy/compose.e2e.yaml` (agora + scribe + SpiceDB + pgvector + stub embedder/LLM); build the Testcontainers Compose harness; seed the demo corpus.
- [ ] Implement the headline permission E2E at the API boundary (alice cannot see bob's doc, revocation on next request, tenant isolation).
- [ ] Implement the SSE assertion for `/api/chat` against the current emitted shape, including the per identity citation check.
- [ ] Promote the DEMO walkthrough into the harness as an always green smoke test.
- Acceptance: a new `e2e` CI job stands up the full stack, drives real `/api/search` and `/api/chat`, and fails on any cross principal leak or demo regression.

### Phase 4: Tier 4 live smoke (per provider, incremental)
- [ ] Stand up the first sandbox (GitHub test org + fine grained PAT), add a `connector_live` test for github, wire OIDC/secret isolation and the drift PR.
- [ ] Add the next sandboxes in maintainer priority order (Salesforce Dev Edition, Notion, Slack, Zendesk, then the gated M365/Atlassian/Google ones as tenants become available).
- Acceptance: the nightly job runs at least one real connector against a real sandbox, diffs against golden cassettes, and opens a PR on drift; it never gates a merge.

### Phase 5: durable ingestion E2E (BLOCKED on task #16)
- [ ] Land the specified crash/resume, idempotent re sync, and replay tests with the DBOS worker (currently skipped placeholders).
- Acceptance: killing the scribe worker mid ingest resumes from checkpoint with exactly once indexing; a duplicate sync is a no op.

### Open questions for the maintainer
1. **Provider test tenant priority.** Which sandboxes to acquire first, given the feasibility split (free/persistent: Salesforce Dev Edition, GitHub test org, Notion, Slack Enterprise Grid; gated/paid: M365 dev tenant, Atlassian Premium sandbox, Zendesk Enterprise, Google Workspace test domain)? Proposed first three: GitHub, Salesforce, Notion.
2. **Conformance engine: keep or retire (Option 2A vs 2B)?** This decides whether every connector ships a `conformance.yaml` or whether the engine is deleted and bespoke tests keep importing the category library. `check_deletions` must be implemented either way.
3. **How much live (credentialed) testing to run?** Nightly only and non blocking is assumed here. Confirm, and confirm the egress allowlist / credential isolation posture for the live job.
4. **SSE protocol convergence.** Keep the current bespoke `{source, text-delta, finish}` SSE shape, or converge `/api/chat` on the Vercel AI SDK UI Message Stream protocol (header `x-vercel-ai-ui-message-stream: v1`, `text-start/delta/end`, `source-url/source-document`, `[DONE]`) that ADR-0002 and lens target? The SSE E2E assertions follow this decision.
5. **E2E job timing.** Run the compose based `e2e` job per PR (slower, catches leaks before merge) or nightly (faster PRs, leaks caught later)? The permission assertions in it argue for per PR; cost argues for nightly.
6. **Notion/Salesforce/SharePoint ACL gaps.** Accept the documented weak ACL fidelity as known limits with failing/xfail tests, or block those connectors from being marked `permission_sync` until the gaps are fixed?

---

## Sources

- [Acceptance Tests Reference, Airbyte Docs](https://docs.airbyte.com/platform/connector-development/testing-connectors/connector-acceptance-tests-reference) (2026): CAT = Spec (+backward compat), Check, Discovery, Basic Read, Full Refresh, and three incremental variants including StateWithAbnormallyLargeValues (far future state must yield zero records); test_strictness_level=high requires every test declared or skipped with an explicit bypass_reason.
- [Incremental Sync, Append + Deduped, Airbyte Docs](https://docs.airbyte.com/platform/using-airbyte/core-concepts/sync-modes/incremental-append-deduped) (2026): cursor based incremental cannot see hard deletes; tombstones require the source to emit deletion records (CDC), so a deletions capability must be tested with an explicit create -> delete -> assert tombstone flow.
- [Slack permission sync ignores connector start date (Issue #9664), onyx-dot-app/onyx](https://github.com/onyx-dot-app/onyx/issues/9664) (2025): a real ACL sync bug that only a behavioral per connector test catches.
- [Access Controls / Connectors Overview, Onyx Documentation](https://docs.onyx.app/admins/connectors/overview) (2025): Auto Sync Permissions mirrors source ACLs so users see only what they could access natively; the anti oversharing model an ACL fidelity test asserts against.
- [pytest-recording, kiwicom](https://github.com/kiwicom/pytest-recording) (2025): VCR.py cassette auto resolution, filter_headers/filter_query_parameters redaction, --block-network hermeticity, record modes once/rewrite/all.
- [VCR.py usage docs, vcrpy 8.0.0](https://vcrpy.readthedocs.io/en/latest/usage.html) (2025): record_mode=all forces re recording (the drift mechanism); strict request matching catches drift, loose matching masks it.
- [Pytest with respx and vcr](https://rogulski.it/blog/pytest-httpx-vcr-respx-remote-service-tests/) (2025): respx for precise mock assertions, VCR cassettes for realistic end to end payloads.
- [Automated Contract Testing: Detect API Drift](https://medium.com/@instatunnel/automated-contract-testing-how-to-detect-api-drift-before-it-reaches-production-6c2a77baa2a3) (2026): spec diffing with oasdiff in CI and scheduled verification so drift is caught before a customer bug report.
- [OpenID Connect, GitHub Docs](https://docs.github.com/en/actions/concepts/security/openid-connect) (2026): short lived job scoped OIDC tokens constrained by exact match sub claim, instead of static secrets.
- [Best Practices for Managing Secrets in GitHub Actions, Blacksmith](https://www.blacksmith.sh/blog/best-practices-for-managing-secrets-in-github-actions) (2026): OIDC for cloud, stored secrets only for third party keys without OIDC, environment secrets with required reviewers, pin actions to full commit SHA.
- [Developer sandboxes, Slack Developer Docs](https://slack.com/help/articles/27390391126803-Manage-developer-sandboxes-on-Enterprise-Grid) (2025): free Enterprise Grid dev sandbox via the Developer Program, 3 day retention, SSO via Simple IdP.
- [Salesforce Developer Edition quickstart](https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/quickstart_dev_org.htm) (2025): free and persistent Developer Edition; the most accessible truly free connector test tenant.
- [Fine grained personal access tokens, GitHub Blog](https://github.blog/security/application-security/introducing-fine-grained-personal-access-tokens-for-github/) (2025): fine grained PATs scope to a single org and specific repos; ideal for a dedicated test org.
- [About Zendesk sandbox environments, Zendesk help](https://support.zendesk.com/hc/en-us/articles/6150628316058-About-Zendesk-sandbox-environments) (2026): Enterprise only sandbox that deactivates webhooks and rewrites user emails to @example.com.
- [RAG Permission Testing with Testcontainers and SpiceDB, Docker Blog](https://www.docker.com/blog/rag-permission-testing-testcontainers-spicedb/) (2025): the reference E2E pattern, real SpiceDB in Testcontainers, seed persona tuples, assert at the API boundary each persona retrieves only authorized docs; OpenAI uses SpiceDB for 37B docs.
- [AI SDK UI: Stream Protocols, Vercel](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol) (2025): UI Message Stream parts (text-start/delta/end, source-url, source-document, tool lifecycle, start-step/finish-step, finish, [DONE]) and the required x-vercel-ai-ui-message-stream: v1 header.
- [Docker Compose Module, Testcontainers](https://java.testcontainers.org/modules/docker_compose/) (2025): ComposeContainer (Compose v2) loads an entire compose stack with listening port wait strategies, reusing dev compose files.
- [Docker Compose Testing for Startups, Autonoma](https://getautonoma.com/blog/docker-compose-testing) (2025): add if: always() teardown plus a timeout so failed runs do not leak containers/volumes; ephemeral per PR cloud envs are the graduation path.
- [Secure multi tenant RAG with Bedrock and Verified Permissions, AWS](https://aws.amazon.com/blogs/architecture/secure-multi-tenant-rag-with-amazon-bedrock-and-verified-permissions/) (2025): enforce document level access at retrieval time; layer defenses so a single misconfiguration does not leak.
- [RAG with Access Control, Pinecone](https://www.pinecone.io/learn/rag-access-control/) (2025): pre filter is more efficient for large low hit corpora; post filtering means the data was already fetched, so prefer pre retrieval enforcement.
- [Testing Your App, DBOS Docs](https://docs.dbos.dev/python/tutorials/testing) (2026): reset runtime between tests (destroy -> new config -> reset_system_database -> launch), SQLite to cut deps, fork_workflow(id, step) to test resume from step; steps may run twice across crashes so must be idempotent.
- [Error handling in distributed systems, Temporal](https://temporal.io/blog/error-handling-in-distributed-systems) (2025): activities are at least once; idempotent implementation yields effective exactly once via an idempotency key with TTL.
- [Replay Testing to Avoid Non Determinism, Bitovi](https://www.bitovi.com/blog/replay-testing-to-avoid-non-determinism-in-temporal-workflows) (2025): replay recorded histories in CI to catch non determinism before it corrupts crash recovery.
- [SSE Pattern, LangWatch Scenario](https://langwatch.ai/scenario/examples/testing-remote-agents/sse/) (2025): POST with Accept: text/event-stream, buffer incomplete lines, strip data: prefix, skip the terminal sentinel.
- [How to re-use E2E tests as smoke tests, Serverless First](https://serverlessfirst.com/emails/how-to-re-use-some-of-your-e2e-tests-as-smoke-tests/) (2025): tag read only E2E tests so they double as always green smoke tests, the mechanism for making the demo unable to rot.
