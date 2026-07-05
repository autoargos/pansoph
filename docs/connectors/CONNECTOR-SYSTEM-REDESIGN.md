# Connector system redesign

**Status:** Blueprint, accepted direction
**Date:** 2026-07-05
**Grounded in:** ADR-0001 (modular knowledge platform, ground-up RBAC), ADR-0002 (finalized stack, minimal AuthzPort, SpiceDB default), and five subsystem audits (RBAC enforcement, connector contract, connect/discovery UX, per-connector fidelity, conformance and quality gates).

This document is the single source of truth for what the connector plus enforcement system is today, what it must become, and the exact ordered plan to get there. It is honest about the current state on purpose. The headline promise (pansoph CLAUDE.md section 1: "a user can only ever retrieve what they are already allowed to see, across every connected source") is not met today, and this document says why and how it will be.

No em dashes anywhere, per the writing convention.

---

## Part 1. State of the system, per subsystem, no spin

### 1.1 RBAC enforcement at retrieval

**Verdict: BROKEN in one load-bearing dimension. Not production ready. Blocks open source.**

The group path and the public path are correct and match the ADRs. `mappers.py:50-55` maps `public` to a tenant-scoped `__pansoph_everyone` group, not a global `user:*` wildcard, so tenant isolation for public docs holds at the authz layer. Groups are tenant namespaced (`mappers.py:48,57`). The overfetch, bulk check, deny path in `packages/pansoph-retrieval/src/pansoph_retrieval/pipeline.py:35-82` is shaped exactly as ADR-0002 line 110 requires: embed, coarse pre filter, overfetch 4x, one `bulk_check` at `at_least_as_fresh`, keep the allowed subset, apply deny, rerank. ZedToken freshness is threaded correctly (`pipeline.py:51-62` -> `adapters/spicedb.py:173-177`).

The defect: **every direct-user grant is structurally unreachable.** `mappers.py:47,59` writes an `allow_users` reader tuple with subject namespace `user_ns = acl.principal_ns or instance_id`, which SpiceDB renders as object id `f"{ns}_{id}"` (`adapters/spicedb.py:40-43`), for example `inst_alice`. But the querying subject is built with `ns=""` at `packages/pansoph-core/src/pansoph_core/routes.py:144` and `packages/pansoph-core/src/pansoph_core/mcp_server.py:35`, so `bulk_check` checks `user:alice` (`adapters/spicedb.py:180`). `document reader user:inst_alice` never matches a check for `user:alice`. OpenFGA has the identical scheme (`adapters/openfga.py`). A document granted only via `allow_users` (which is the sole grant emitted by Slack, Gmail, Teams, and the direct-user path of Drive, Dropbox, Confluence, SharePoint, Jira, Notion, Salesforce, GitHub) is retrievable by nobody. This is a denial bug, not a leak, but it means the single most common ACL shape does not work and the requested "public repo doc scoped to alice, denied to bob" cannot even grant alice.

Worse, the release gate cannot see it. The default CI red team (`tests/permission/test_redteam.py`) runs against `tests/_fakes.py`, whose `FakeAuthz.bulk_check` ignores `Principal.ns` entirely and is explicitly tenant blind. It would accept a direct-user grant the real SpiceDB rejects, and its cross-tenant test passes only because `FakeVectorStore` pre-filters on tenant, which ADR-0002 line 48 and CLAUDE.md rule 3.1 forbid as a source of truth. The only suite that proves real authz-layer tenant isolation and ZedToken revocation (`tests/integration/test_enforcement_live.py`) is `@pytest.mark.integration` and skips unless `PANSOPH_SPICEDB_ENDPOINT` and `PANSOPH_PG_DSN` are set, so most runs skip it.

Two more real but lower-severity issues. Deny is filtered against only the subject's direct groups (`pipeline.py:66-77`), so a deny targeting an inherited parent group is not honored. The `user:*` wildcard is dead in the mapper but still declared in the schema (`adapters/spicedb.py:33`) and special-cased in `_ns_id` (`adapters/spicedb.py:41-43`), a latent footgun that would grant globally across tenants if any code ever wrote `id="*"`.

### 1.2 Connector contract and the declarative manifest runtime

**Verdict: PARTIAL. The Python contract is production grade. The declarative tier is a facade. Not open-source ready as advertised.**

The hand-written contract is faithful and clean: the `Connector` Protocol matches ADR-0001 lines 66-74 (validate, list_changes, fetch to RawDoc plus Acl, list_acls, on_webhook), `Capabilities` has exactly the four ADR flags, `AccessControl` matches the ADR-0001 data shape (`pansoph_types/__init__.py`). The `ConnectorSpec` plus `FieldKind` secret-sealing design is a genuine security-by-construction win.

But the ADR-0002 line 56 headline "declarative manifest first" is inverted in practice. The manifest compiler raises `NotImplementedError` for more than one stream, does a single GET with no pagination despite a `PaginatorSpec` existing, ignores `list.since` and `list.cursor` so there is no real incremental, and never emits tombstones despite letting a manifest declare `deletions: true`. All 17 shipped connectors are hand-written Python; zero use the compiler. The one manifest in the tree (`connectors/confluence/manifest.yaml`) is dead placeholder text pointing at `example.com`, referenced by no code, while Confluence actually ships a 232-line Python connector. Per CLAUDE.md rule 6 (no fiction), advertising a tier that cannot satisfy any capability it permits a manifest to declare is a bug. The built `auth_requirement`/`token_source` refreshing-credential convention is wired into zero connectors, so the resumable OAuth path has no live proof.

### 1.3 OAuth, discovery, and connect-then-configure UX

**Verdict: BROKEN for the flagship connector. Not production ready.**

The OAuth connect path cannot produce a working GitHub instance. In `apps/lens/src/pages/Connectors.tsx`, the connect button does `window.location.href = url` and navigates away before the form submit runs, so the typed owner/repo are discarded. The callback (`packages/pansoph-core/src/pansoph_core/oauth/flow.py`) seals only the token into config and calls `create_instance`, then `GithubConnector.validate` raises "missing config: owner, repo". The flow 500s at the callback and no instance is created. The sequencing is backwards: it conflates "connect a credential" with "create a fully configured instance" in one call, but the resource the user must pick (repo, drive, space) needs the credential to exist first.

There is no resource-discovery endpoint anywhere. The entire connector HTTP surface (`routes.py`) is specs, instance CRUD, sync, runs, and oauth authorize/callback. No route lists the orgs, repos, drives, spaces, or channels a connected credential can reach. Every resource selector is a free-text `ConfigField` (github owner+repo, drive folder_id+drive_id, confluence space_key), which for Drive and Confluence are opaque ids a human cannot know. Slack has no scope field and ingests every reachable channel. Re-auth is offered unconditionally on every form open because there is no endpoint reporting credential status (connected, expires_at, needs_reauth).

### 1.4 Per-connector source-ACL fidelity

**Verdict: PARTIAL. ACL mapping is mostly faithful and honestly documented, but two systemic defects make the headline feature half real. Not production ready.**

Systemic defect one: deletion and revocation propagation is not wired end to end. `pipeline.py:58-64` (ingestion `sync`) and the DBOS workflow iterate every `DocRef` and call `ingest_one` without ever checking `ref.deleted`. `ingest_one` (`pipeline.py:85`) always calls `write_tuples` with the full ACL and never calls `diff_acls` or `delete_tuples`. `diff_acls` (`mappers.py:85-102`) is implemented and exported but has zero production callers. Net effect: a revoked or deleted source document stays indexed and its old reader tuples stay in SpiceDB, so it remains retrievable. This defeats the ADR-0002 revocation guarantee and CLAUDE.md rule 1.

Systemic defect two: false capability declarations. GitHub, Google Drive, Confluence, Dropbox, SharePoint, and Zendesk declare `incremental=True` and/or `deletions=True` but never yield a non-empty cursor or a tombstone. No connector test imports `check_incremental` or `check_deletions`, and no connector calls `assert_conforms`, so the false claims ship untested. The runtime trusts capabilities, so these connectors will be scheduled as incremental/deletion capable and silently do neither. `InMemoryConnector` is the only connector that truly implements both.

Correctness holes beyond the systemic ones: Confluence ignores ancestor-inherited restrictions (child-page leak). SharePoint lists only `/root/children` non-recursively (drops subfolder docs) and uses inconsistent principal ids. Salesforce maps only explicit shares, so Public-OWD objects become unreadable to everyone. Jira uses project Browse role only, over-sharing issue-security-restricted issues. Slack, Linear, and Notion do not check API error envelopes (HTTP 200 with `{ok:false}` or an errors array yields a silently wrong ACL). Zendesk marks null-segment articles public without a draft check. `acl_version` is hardcoded to 1 (`pipeline.py:97`), so cache invalidation is non-functional. Honest and correct by design: Gmail (owner-only), Notion, weblink, mcp (operator ACL, `permission_sync=False` with loud caveats).

### 1.5 Conformance and permission red team gates

**Verdict: PARTIAL. A real red team exists and gates CI, but "one shared conformance suite for every connector" is half built. Not sufficient to open source.**

Real and good: `tests/permission/test_redteam.py` runs the real pipeline plus mappers plus `retrieve()` and asserts cross-user, cross-tenant, deny-overrides, and revocation. CI has a dedicated `permission-gate` job and a hard `enforcement-live` job that brings up real SpiceDB plus pgvector via `deploy/compose.test.yaml` with no `|| true`. The live suite proves the pre-filter is not the source of truth, nested-group inheritance, tenant-scoped public, and ZedToken revocation freshness. These are the right properties.

Half built: the yaml-driven `run_conformance` engine (`verify/engine.py`) is dead code with zero `conformance.yaml` files and zero callers. Connectors cherry-pick `check_spec`, `check_discovery`, `check_permissions` directly, and none run `check_incremental` or `check_deletions`, so the "declares deletions=True but never emits a tombstone" hole ships uncaught. ACL-mapping fixtures are one document per connector, no deny case, no nested groups, no org-vs-personal credential scope, no oversharing regression. There is no revocation-latency (time-bound) test. The nightly `connector-live-smoke` is a no-op because zero integration-marked tests exist in connectors. mypy is non-blocking (`|| true`) even on the enforcement-critical packages.

### 1.6 Summary verdict

The retrieval enforcement engine is well designed and, for group and public docs against real SpiceDB, genuinely enforced. But the system as a whole is **not production ready and not open-source ready**, because: direct-user ACLs are silently dead across roughly ten connectors and invisible to the default gate; connector scope is inert; deletion and revocation are unwired; six connectors lie about capabilities; the flagship OAuth flow does not produce a working connector; there is no resource discovery; and the release gate can false-green.

---

## Part 2. Target architecture

Everything below is grounded in ADR-0001 (data shapes, five bets, connector contract) and ADR-0002 (minimal AuthzPort, SpiceDB default, ports and adapters, one shared conformance suite). Ports stay minimal. No new heavy dependency is introduced.

### 2.1 RBAC scope enforcement: scope narrows the ACL, enforced at retrieval

The core correction: **an instance's scope is an intersection over the source ACL, never orthogonal to it, and it is applied in ingestion before tuples are written.** The narrowed audience is always a subset of the source ACL, so scope can only remove reach, never add it. The misleading comment at `connectors.py:57-59` calling scope "orthogonal to the source ACL" is deleted.

Two coordinated changes make this real.

**(a) One principal per tenant for users, so direct-user grants resolve.** This is the P0 fix (Part 4). `tuples_for_acl` in `packages/pansoph-authz/src/pansoph_authz/mappers.py` namespaces `allow_users` by tenant, exactly as groups already are, and the query subject in `routes.py:144` and `mcp_server.py:35` is built with `ns=tenant_id`. A person is one principal per tenant, not per connector instance, which mirrors the group and public design and is the consistent choice. After this, an `allow_users=[alice]` grant is checkable as `user:{tenant}_alice` and matches the query subject.

**(b) Scope narrowing in ingestion.** A pure function `narrow(acl, scope_type, scope_ref) -> AccessControl` is threaded from `run_instance` (`packages/pansoph-core/src/pansoph_core/connectors.py:281-311`) into `IngestionPipeline.ingest_one` (`packages/pansoph-ingestion/src/pansoph_ingestion/pipeline.py:66-105`) and applied after `fetch`, before `tuples_for_acl`:

- `org`: acl unchanged.
- `individual` (`scope_ref` is a user id): the effective audience is the source ACL intersected with `{scope_ref}`. Set `public=False`, `allow_groups=[]`, and `allow_users=[scope_ref]` only if `scope_ref` was reachable in the source ACL (present in `allow_users`, or the doc was public, or `scope_ref` is a member of one of `allow_groups`). Otherwise the audience is empty (the doc is indexed but readable by no one, the safe closure). `deny` is preserved.
- `group` (`scope_ref` is a group id): set `public=False`, `allow_users=[]`, `allow_groups=[scope_ref]` only if `scope_ref` was reachable (present in `allow_groups`, or the doc was public). Otherwise empty.

The invariant is pure set intersection. This makes "a public-repo instance scoped to individual=alice" produce `allow_users=[alice]`, which after change (a) grants alice and denies bob at the SpiceDB bulk check.

**(c) Harden the rest of the enforcement path.** Delete `user:*` from the SpiceDB and OpenFGA schemas and the wildcard special cases so a stray `id="*"` can never grant globally. Strengthen deny to evaluate against the transitive group closure the engine used (or a second `bulk_check` against deny tuples). Make the `minimize_latency` fallback (`adapters/spicedb.py:177`) an explicit audited downgrade, or refuse to serve a token-less candidate in a strict mode. Move membership sync (`routes.py:136-141`) off the retrieval hot path into the Identity port on login or SCIM per ADR-0002, keeping only a cache-warm check.

### 2.2 Resource discovery and connect-then-pick UX

**Two-phase instance lifecycle.** A `status` column is added to `connector_instances` (default `active`, plus `draft`). OAuth authorize/callback creates or updates a `draft` instance that holds only the sealed credential, and `validate_connector_config` (`connectors.py:63`) skips resource-required validation when `status='draft'`. The callback redirects to `/connectors?configure={instanceId}`, and lens opens the form in a configure step for that draft, where the user picks resources and the instance is promoted to `active`. This fixes the flagship break: token first, resources second, against that exact token.

**A discovery capability on the contract.** `Capabilities` (`pansoph_types/__init__.py:33-39`) gains a fifth additive flag `resource_discovery: bool = False`. The `Connector` Protocol gains one optional method:

```
async def list_resources(self, kind: str | None, parent_id: str | None, cursor: str | None) -> ResourcePage
```

returning `Resource{id, kind, label, parent_id?, selectable, extra}` and a `next_cursor`. It enumerates only what the configured credential can actually reach, at the source's granularity, paginated by cursor and hierarchical by `(kind, parent_id)` so the UI drills org to repo, drive to folder, workspace to channel. It reuses the same `TokenSource` as sync (`_build_connector`/`_attach_auth`, `connectors.py:314-339`), so discovery is a read the credential already permits and never a scope escalation. Conformance enforces the pairing exactly like deletions: declaring `resource_discovery` requires implementing `list_resources`.

**Config fields become resource-backed.** `ConfigField` gains `resource_kind: str | None`, so github owner/repo, drive folder_id/drive_id, confluence space_key, and a new slack `channel_ids` list bind to a picker instead of free text. The field stays free-text as a fallback when the flag is off, so the whole thing is optional and back-compatible for air-gapped/PAT users.

**Backend endpoint.** `GET /api/connectors/instances/{instance_id}/resources?kind=&parent=&q=&cursor=`, gated by the connector-admin role, resolves the (possibly draft) instance, resolves sealed secrets, builds the connector, calls `list_resources`, and returns items plus `next_cursor`. Server-side `q` filtering when the source lacks search, cursor pagination passed through, 400 when the connector declares no discovery, 502 mapping upstream errors (never swallowed). Every call is audited.

**Credential-status endpoint.** `GET /api/connectors/instances/{instance_id}/credential` returns `{connected, provider, account_label?, expires_at?, has_refresh, needs_reauth}` computed from the sealed handles and the stored expiry plus whether a refresh token exists (`oauth/auth_requirement.py`). lens shows "Connected as X, valid until Y" with a subordinate Reconnect link when connected and not needing reauth, and only shows the prominent Connect button when no valid credential exists. This ends the every-open re-prompt.

Per-connector discovery is implemented against current official docs (CLAUDE.md rule 6, verified date recorded in each module): github (`/user/orgs`, `/user/repos`, `/orgs/{org}/repos`, Link pagination), google_drive (`drives.list`, `files.list` folder query, `supportsAllDrives`), confluence (`accessible-resources` for cloudid then `/wiki/api/v2/spaces`), slack (`conversations.list` with cursor). Each returns human labels plus the opaque id the connector needs, and lists only what the credential reaches so ACL mapping stays faithful.

### 2.3 Production-grade per-connector requirements and faithful source-ACL mapping

**Deletion and revocation wired end to end.** `IngestionPipeline.sync` and the DBOS workflow branch on `ref.deleted`: purge the vector document and call `authz.delete_tuples` for its tuples. `ingest_one` loads the prior ACL for the doc and uses `diff_acls` (`mappers.py:85-102`, revocations first) instead of a blind `write_tuples`. `acl_version` (`pipeline.py:97`) becomes monotonic and real.

**No false capabilities.** For github, google_drive, confluence, dropbox, sharepoint, zendesk, either implement real incremental (real cursors) and deletions (tombstones) or set the flags `False`. Then every connector calls `assert_conforms`, so `check_incremental` and `check_deletions` actually gate them.

**Faithful source-ACL mapping (CLAUDE.md rule 6.3).** Each connector maps ACLs to whatever the credential can reach, at the granularity the source exposes, and records the doc links and verified date. Fixes: Confluence unions ancestor restrictions; SharePoint lists children recursively (or uses delta) and normalizes principal identity to one stable id; Salesforce maps OWD-Public to a tenant group and resolves role hierarchy or is explicitly downgraded to best-effort; Jira resolves issue-level security or is downgraded; slack/linear/notion check API error envelopes before trusting an ACL; zendesk adds a draft check; mcp bridge gains a server-auth token field.

**The manifest tier is resolved honestly.** Either implement multi-stream, real pagination, since/cursor incremental, and tombstone deletions in the compiler and migrate three to five genuinely simple REST connectors to prove it, or accept Python-first, update ADR-0002 line 56 and CLAUDE.md to say "Python-first with a declarative option", and delete the dead `confluence/manifest.yaml`. Do not ship the half-manifest as if it satisfies the ADR.

### 2.4 Conformance and permission red-team gates that must pass before open sourcing

One shared conformance suite becomes mandatory and category-complete. Either delete `verify/engine.py` `run_conformance` or make it the only path: every connector ships a `conformance.yaml`, a parametrized test discovers them all via entry points and runs `run_conformance`, and bespoke cherry-picking is banned. Capability-gated categories become required: if `caps.incremental` or `caps.deletions` or `caps.resource_discovery` is declared, the corresponding category must run and pass. ACL-mapping fixtures become a required matrix per connector: at least one public, one group-scoped, one user-scoped, one explicit deny, and where the source supports it one doc under a different credential scope, each asserting set-equality and per-principal visibility including a negative principal, plus a per-connector oversharing regression.

The release gate set that must be green (all hard, no `|| true`) before open sourcing:

- **permission-gate**: cross-user, cross-tenant, deny, revocation correctness. Exists, keep. Add the direct-user end-to-end case (the P0 regression) to both the fake and the live suite.
- **enforcement-live**: real SpiceDB plus pgvector, pre-filter-not-source-of-truth, nested-group, ZedToken revocation freshness. Exists, keep, and make it required (not skip-by-default) by fixing `tests/_fakes.py` `FakeAuthz` to honor `Principal.ns` and tenant so the fast suite stops false-greening.
- **conformance-gate**: the one shared suite over every connector with incremental, deletions, discovery, and the ACL matrix required. Needs the engine wired and fixtures deepened.
- **revocation-latency-gate**: new. Revoke, re-sync, assert the doc disappears within a bounded number of workflow steps or a wall-clock budget.
- **grabber-guardrail-gate (scout)**: asserts no auth bypass, robots honored, rate limits honored (ADR-0001 line 165).
- **mypy-gate**: flip `|| true` off for pansoph-authz, pansoph-retrieval, pansoph-ingestion (the enforcement path).
- **supply-chain-gate**: SBOM plus Grype/Trivy scan plus cosign on release.

---

## Part 3. Implementation plan, phased and ordered

Each phase is independently shippable and independently verifiable. The permission red team (specifically the direct-user, scope-narrowing, and revocation cases against real SpiceDB) is the gate that closes each security-relevant phase. Parallelism is called out.

### Phase 0 (the immediate fix, security-blocking): direct-user grants resolve

See Part 4 for the exact change. Files: `packages/pansoph-authz/src/pansoph_authz/mappers.py` (namespace `allow_users` by tenant), `packages/pansoph-core/src/pansoph_core/routes.py:144` and `packages/pansoph-core/src/pansoph_core/mcp_server.py:35` (build the query subject with `ns=tenant_id`). Add a direct-user end-to-end test to `tests/permission/test_redteam.py` and `tests/integration/test_enforcement_live.py`. **Verify:** bob denied, alice allowed on an `allow_users=[alice]` doc against real SpiceDB.

### Phase 1 (gate integrity, can run parallel to Phase 2): make the release gate honest

Fix `tests/_fakes.py` `FakeAuthz.bulk_check` to honor `Principal.ns` and tenant. Promote `tests/integration/test_enforcement_live.py` to a required CI job (SpiceDB plus pgvector already exist in `deploy/compose.test.yaml`). Delete `user:*` from `adapters/spicedb.py:33` and `adapters/openfga.py` schemas and the wildcard special cases (`adapters/spicedb.py:41-43`). **Verify:** the fake suite now fails on the pre-Phase-0 direct-user defect if reintroduced; the live job is required.

### Phase 2 (headline feature, depends on Phase 0): deletion and revocation wired end to end

Files: `packages/pansoph-ingestion/src/pansoph_ingestion/pipeline.py` (`sync` branches on `ref.deleted`, `ingest_one` uses `diff_acls` and purges on delete, `acl_version` monotonic), `packages/pansoph-ingestion/src/pansoph_ingestion/workflow_dbos.py` (same `ref.deleted` handling), `packages/pansoph-authz/src/pansoph_authz/mappers.py` (`diff_acls` gains its first production caller). Add the revocation-latency test. **Verify:** ingest, revoke, re-sync, assert non-retrieval within the step budget against real SpiceDB.

### Phase 3 (scope enforcement, depends on Phase 0): scope narrows the ACL

Files: new `narrow(acl, scope_type, scope_ref)` in `packages/pansoph-ingestion` (or `pansoph-authz` alongside the mappers), threaded from `packages/pansoph-core/src/pansoph_core/connectors.py:281-311` (`run_instance` reads `scope_type`/`scope_ref`) into `IngestionPipeline.ingest_one`. Delete the "orthogonal" comment at `connectors.py:57-59`. Add permission tests: public doc plus individual scope alice (alice allowed, bob denied), group scope, and scope-can-never-widen. **Verify:** re-index a public-repo instance scoped to individual=alice, bob denied, alice allowed.

### Phase 4 (UX, largely parallel to 2 and 3): connect-then-pick and re-auth suppression

Sub-phases, each shippable:

- 4a: two-phase lifecycle. Add `status` column, `draft` handling in `connectors.py` and the oauth callback (`oauth/flow.py`), callback redirect to `?configure=`. Fixes the flagship break independent of discovery. **Verify:** OAuth GitHub yields a draft, then an active instance with owner/repo.
- 4b: discovery capability. `resource_discovery` flag (`pansoph_types`), `list_resources` on the contract, `resource_kind` on `ConfigField`, the `.../resources` endpoint in `routes.py`, and the conformance discovery gate. Per-connector `list_resources` for github, google_drive, confluence, slack (each against current docs, verified date recorded). Can be parallelized per connector once the contract and endpoint land.
- 4c: credential-status endpoint and re-auth suppression in lens.
- 4d: the lens `ResourcePicker` component and the `?configure=` step.

### Phase 5 (fidelity, parallel per connector): faithful ACL and honest capabilities

Per connector, independently: remove or implement false incremental/deletions caps and wire `assert_conforms`; fix the leak-capable connectors (confluence ancestor restrictions, sharepoint recursive listing plus principal normalization); add API error-envelope checks (slack, linear, notion); zendesk draft check; jira issue-security and salesforce OWD (or explicit best-effort downgrade); mcp server-auth. Deepen ACL fixtures to the required matrix. **Verify:** every connector passes `assert_conforms` including the oversharing regression.

### Phase 6 (tier honesty and final gates): resolve the manifest tier and lock the release gate set

Decide and execute the manifest resolution (implement or re-scope and delete the dead confluence manifest). Wire the full release gate set from section 2.4: conformance-gate, revocation-latency-gate, grabber-guardrail-gate, mypy-gate on the enforcement path, supply-chain-gate. **Verify:** all seven gates green with no `|| true`, then open source.

**Parallelism summary:** Phase 0 first, alone. Then Phase 1 (gate integrity) runs alongside Phase 2 (revocation) and Phase 3 (scope), which both depend only on Phase 0. Phase 4 UX runs alongside 2 and 3. Phase 5 fidelity is per-connector parallel. Phase 6 is the final serialization point before open sourcing.

---

## Part 4. The immediate fix: make scope enforce RBAC by making direct-user grants resolve

The scope-narrowing intersection in Part 2.1 produces `allow_users=[alice]` for an individual-scoped instance. That grant only enforces if a direct-user tuple is checkable, and today it is not. The immediate, minimal fix is to align the direct-user namespace on both sides.

**File 1: `packages/pansoph-authz/src/pansoph_authz/mappers.py`.** Namespace `allow_users` by tenant, exactly as groups already are. Change the user namespace in `tuples_for_acl` from the instance-scoped `user_ns` to the tenant-scoped `group_ns`:

```python
# before (line 47, 58-59): user tuples are instance/principal_ns scoped
user_ns = acl.principal_ns or instance_id
...
for user in acl.allow_users:
    out.append(_reader(Principal(type="user", id=user, ns=user_ns), object_id))

# after: user tuples are tenant scoped, matching groups and the query subject
for user in acl.allow_users:
    out.append(_reader(Principal(type="user", id=user, ns=group_ns), object_id))
```

`group_ns` is already `tenant_id` when a tenant is passed (`mappers.py:48`). A person is one principal per tenant, so alice resolves against every instance's direct-user grants in her tenant. `diff_acls` inherits this automatically because it calls `tuples_for_acl`.

**File 2 and 3: the query subject gets the same namespace.** In `packages/pansoph-core/src/pansoph_core/routes.py:144` and `packages/pansoph-core/src/pansoph_core/mcp_server.py:35`, build the subject with `ns=tenant_id`:

```python
# before
subject=Principal(type="user", id=user)
# after
subject=Principal(type="user", id=user, ns=tenant_id)
```

Now `_ns_id` renders both the tuple subject and the query subject as `user:{tenant}_alice` (`adapters/spicedb.py:40-43,180`), and the bulk check matches.

**What AccessControl becomes for scoped instances (Phase 3 rides on this fix).** After `narrow`: for `scope_type='individual', scope_ref='alice'` on a source doc that was public or granted to alice, the ingested `AccessControl` is `public=False, allow_groups=[], allow_users=['alice'], deny=<preserved>`. For `scope_type='group', scope_ref='eng'`, it is `public=False, allow_users=[], allow_groups=['eng']`. For any scope where `scope_ref` was not reachable in the source ACL, the audience is empty and the doc is readable by no one.

**How to re-index and prove bob is denied on the Liber docs.** With File 1 to 3 landed and Phase 3's `narrow` threaded, set the Liber instance `scope_type='individual', scope_ref='alice'` (or the operator's own user id), then re-run the sync so `ingest_one` re-derives tuples with the narrowed ACL. Because the source public grant is intersected to `allow_users=['alice']`, the mapper writes `document reader user:{tenant}_alice` and no public/everyone tuple. Then:

1. Query as alice (`Principal(type='user', id='alice', ns=tenant)`): the bulk check returns the Liber doc ids. Allowed.
2. Query as bob (`Principal(type='user', id='bob', ns=tenant)`): no reader tuple matches `user:{tenant}_bob`, the doc is not public, so `bulk_check` excludes every Liber doc id. Denied.

Run this as the direct-user regression in `tests/permission/test_redteam.py` (fast, fixed fake once `FakeAuthz` honors `ns`) and in `tests/integration/test_enforcement_live.py` (real SpiceDB), so the previously invisible defect can never return silently.

---

**Bottom line:** the enforcement engine is sound in shape but broken in three load-bearing places (direct-user namespace, inert scope, unwired revocation) and blind at the gate (a namespace-and-tenant-blind fake). Fix the namespace first, make the gate honest, wire revocation and scope, then build discovery and connector fidelity on top, and gate the whole thing with a category-complete conformance suite plus a real permission red team before open sourcing.
