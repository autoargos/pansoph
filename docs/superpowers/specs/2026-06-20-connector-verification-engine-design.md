# Connector verification engine and declarative manifest compiler

Status: design approved, ready for implementation planning
Date: 2026-06-20
Scope: pansoph sub-project A (of a 6-part decomposition)
Owner: Yash

## 1. Problem and goal

pansoph's reason to exist is document level access control enforced at retrieval, across every connected source. A connector's job on that front is to faithfully report each document's content and its source access control list. If a connector mis-maps a source's permissions, the entire ReBAC guarantee breaks silently at the source boundary, before retrieval enforcement ever runs.

Today the connector situation undercuts that promise:

- Five connectors exist. GitHub (~277 LOC), Google Drive (~170), and Confluence (~127) have real, doc cited API code. Granola is a 34 line MCP bridge wrapper. weblink is a 65 line stub with no test directory.
- Every connector conformance test is `@pytest.mark.skip` with a TODO body. There are zero recorded fixtures, zero live runs, and the `integration` marker is defined but unused.
- The shared conformance suite (`assert_conforms`) checks shape only. It asserts that an `AccessControl` is returned, never that it is the correct one.
- `compile_manifest()` raises `NotImplementedError`. The declarative manifest path is a schema with no compiler.
- The release workflow has no permission gate wired to connector ACL correctness.

The goal of this spec is a connector verification engine plus a working declarative manifest compiler that together make connectors trustworthy and cheap to author, with ACL mapping correctness gated for every connector. This is the keystone that unblocks the later push to add 20 high ROI connectors: you cannot credibly ship 20 connectors if you cannot prove one.

## 2. Decisions locked during brainstorming

1. Verification posture: both, layered. Hermetic recorded fixture conformance gates every PR. A separate opt in live smoke suite proves reality and regenerates fixtures.
2. Scope: both the verification harness and the declarative manifest compiler, in one spec.
3. ACL correctness: mandatory and gating for every connector, the same way the release permission gate works.
4. Approach: in house CAT style engine plus a minimal in house manifest interpreter, modeled on Airbyte's patterns but scoped to pansoph. No dependency on airbyte-cdk, because it is a heavy framework that owns its own read loop and would fight pansoph's durable execution seam and the building blocks ethos.

## 3. Research summary

Top sources: Airbyte Connector Acceptance Tests (CAT) reference and low code CDK docs; airbyte-python-cdk (`ManifestDeclarativeSource` to `ModelToComponentFactory`); VCR.py 8.x with pytest-recording, respx, syrupy; Onyx/Danswer `external_permissions/<connector>/doc_sync.py` and `group_sync.py`; Microsoft Graph connector `externalItem` ACL shape; Glean permissions docs.

Key findings:

- CAT is the proven shape: a connector agnostic suite driven by a per connector YAML that separates config from expectations. Expected data is declared as JSONL/JSON files, not coded. Categories: spec, connection, discovery/schema, basic read, full refresh, incremental (cursor monotonicity plus abnormally large state yields empty).
- Record and replay stack: VCR.py 8.x with pytest-recording handles async httpx, and `record_mode="none"` is the offline CI safety gate (any un recorded request fails). respx covers crafted error, timeout, and deny permutations the real API will not emit. syrupy snapshots normalized output. Secret scrubbing is mandatory before the first recording.
- ACL correctness is a golden set, set equality problem. No project ships a formal ACL correctness harness, but the assertable pattern is: replay a recorded source permission fixture plus a roster of synthetic principals, then assert the computed allowed viewer set per document exactly equals expected, with explicit negative cases and deny precedence. Set equality, not subset, because oversharing is a compliance incident. Onyx's per connector `doc_sync`/`group_sync` layout is the structure to copy.
- Manifest compiler: Airbyte's architecture is pydantic validated models to a `ModelToComponentFactory` that instantiates runtime components, with a `Custom`+`class_name` escape hatch, validated on construct.
- Drift defense: a scheduled live job re records cassettes in `record_mode="all"`, diffs against committed fixtures, and opens a PR on change.

## 4. Architecture and placement

Everything lands in `pansoph-connector-sdk`, which depends only on `pansoph-types`. This keeps the verification engine and compiler a standalone building block and respects pansoph's rule that the conformance suite is the single source of truth and lives there.

```
packages/pansoph-connector-sdk/src/pansoph_connector_sdk/
  verify/
    config.py        # ConformanceConfig pydantic model parsed from conformance.yaml
    engine.py        # run_conformance(config_path); assert_conforms kept as the no fixture path
    categories/      # one module per category: spec, connection, discovery, read,
                     #   incremental, deletions, permissions
    fixtures.py      # cassette and VCR setup, secret scrubbing, fixture and snapshot loading
    principals.py    # synthetic principal roster and the allow/deny resolution helper
  manifest/
    schema.py        # ConnectorManifest and friends, extended (moved from manifest.py)
    compiler.py      # compile_manifest: pydantic models to a component factory
    components/      # requester, selector, paginator, cursor, aclmapper
```

Dependencies added:

- Runtime, one small library: `jsonpath-ng` for the manifest's JSONPath record and ACL expressions. Adapter grade library, no ADR needed per pansoph rules.
- Dev and test group only, never shipped in the runtime install: `vcrpy`, `pytest-recording`, `respx`, `syrupy`.

Deliberately untouched: the `AccessControl` shape in `pansoph-types` stays as is. It already expresses grant (`public`, `allow_groups`, `allow_users`) and contraction (`deny`). Connector ACL verification asserts the connector produces the correct `AccessControl`. Mapping that to authz tuples stays `pansoph-authz`'s separately tested job, so the connector block keeps zero authz dependency.

## 5. The verification engine

### 5.1 Per connector configuration

A `conformance.yaml` per connector separates config from expectations:

```yaml
connector: github
hermetic_config: fixtures/config.json          # safe fake creds for replay
live_config:     secrets/config.json           # gitignored, used only to re-record
fixtures:        fixtures/
categories:
  spec:        {}
  connection:  { expect_ok: true }
  discovery:   {}
  read:        { expected_records: fixtures/expected_records.jsonl }
  incremental: { enabled: true, abnormal_state: fixtures/future_state.json }
  deletions:   { enabled: true }
  permissions:
    source_acl: fixtures/permissions/source_acl.json
    principals: fixtures/permissions/principals.json
    expected:   fixtures/permissions/expected_acl.json
```

`ConformanceConfig` is a pydantic model. The engine skips a capability gated category (incremental, deletions) when the connector does not declare the matching capability. The permission category is never skipped: it is mandatory for every connector. A public only connector ships a permission fixture whose `expected_acl` asserts public visibility, which the resolver still checks against the principal roster (every principal can see the doc). This way "mandatory and gating for every connector" holds uniformly, with no exemption.

### 5.2 Categories

- spec: `meta` is well formed (name, auth, capabilities, version). Declared capabilities are internally consistent.
- connection: `validate(hermetic_config)` returns `ok`.
- discovery: `list_changes(None)` yields well typed `(DocRef, cursor)`, every `DocRef` carries `instance_id`. This is today's `assert_conforms` core, kept.
- read: `fetch(ref)` returns `(RawDoc, AccessControl)`, the `RawDoc` matches the ref, and normalized output snapshot matches `expected_records` via syrupy.
- incremental: replaying with the last cursor yields no already seen doc (kept), plus an abnormally large state yields zero records.
- deletions: a removed source doc surfaces as a delete change.
- permissions: the gated ACL category, detailed in 5.3.

`assert_conforms(connector)` is retained as a convenience that runs the contract categories (discovery, read, incremental) with no fixtures, for the in memory reference and quick unit checks. `run_conformance(config_path)` is the full fixture driven engine.

### 5.3 The permission category

This is the category Airbyte lacks and the one that protects pansoph's reason to exist.

Three fixtures per connector:

- `source_acl.json`: a recorded snapshot of the source's raw permission state (for example a GitHub repo's collaborators and teams, a Confluence page's read restrictions). Captured during live smoke alongside the cassettes.
- `principals.json`: a synthetic roster of users, their group memberships, and tenant, including principals who should see a doc and principals who must not.
- `expected_acl.json`: the asserted expected `AccessControl` per document, the snapshot syrupy guards.

Assertions per document:

1. Set equality of the produced `AccessControl`. The connector's `fetch`/`list_acls` output must exactly equal `expected_acl` on `public`, `allow_groups`, `allow_users`, and `deny`. Not subset. Oversharing (a superset) fails as hard as under sharing.
2. Resolved visibility against the principal roster. Each `AccessControl` is resolved against `principals.json` using the same allow then deny precedence the retrieval path uses (deny always wins, applied as a final filter, never as an authz exclusion tuple). For every principal the test asserts an explicit boolean: principal X can see doc Y, principal Z cannot. Negative assertions are mandatory.
3. Deny precedence. At least one fixture document exercises a principal who is in an allowed group but explicitly denied, asserting they cannot see it.

The permission category runs under `@pytest.mark.permission`, the same standalone CI gate that guards retrieval enforcement today. A connector that lacks permission fixtures, or whose set equality or negative assertions fail, does not merge.

Reference to copy: Onyx's per connector `external_permissions/<connector>/doc_sync.py` and `group_sync.py` layout for how a connector separates per document ACL extraction from group membership resolution. pansoph mirrors that split where a source needs it, but keeps it inside the connector behind the existing `fetch`/`list_acls` contract rather than adding new ports.

### 5.4 Two modes

- Hermetic, the PR gate: VCR `record_mode="none"` replaying scrubbed cassettes with `hermetic_config`. Any un recorded HTTP call fails. Deterministic, offline, no secrets. respx supplies crafted error, timeout, and edge cases the real API will not emit.
- Live smoke, nightly or on demand: `record_mode="all"` with real `secrets/config.json`, which re records cassettes and regenerates expected snapshots, then diffs against committed fixtures and opens a PR on drift.

### 5.5 Secret scrubbing

Mandatory before any cassette is committed: `filter_headers` for authorization and cookie, query parameter filtering, and a `before_record_response` body hook over JSON payloads. A fake key is swapped in for replay, and the real key is read from env only when re recording. A CI check greps committed cassettes for known secret patterns as a backstop.

## 6. The declarative manifest compiler

`compile_manifest()` stops raising and becomes a real pydantic models to component factory pipeline, modeled on Airbyte's `ModelToComponentFactory` but scoped to pansoph. It produces an object satisfying the existing `Connector` protocol, so a compiled manifest is indistinguishable from a hand written connector to the runtime and to the verification engine.

Validate on construct: the manifest is validated against the extended `ConnectorManifest` pydantic schema at compile time, so a bad manifest fails as a clear schema error, never a runtime crash mid sync.

Components the factory instantiates, each a small internal class reused across every declarative connector so they inherit pagination, retry, and cursor behavior for free:

- Requester: httpx calls using the manifest's `auth` (the existing `AuthScheme`) and `base_url`.
- RecordSelector: JSONPath extraction of list and record payloads via `jsonpath-ng`.
- Paginator: the common strategies only: page/offset, cursor (next token in body), and link header. These three cover the existing GitHub, Drive, and Confluence patterns. More can be added by demand.
- Cursor: datetime based incremental sync from the manifest's `since` and `cursor` fields, including client side filtering for sources that ignore a since parameter.
- AclMapper: the existing `AclMapping` (JSONPath `groups`, `users`, `public`, plus a new `deny` expression) turned into an `AccessControl`. This is the security critical piece, verified by the section 5.3 permission category exactly like a hand written connector.

The durable runtime seam is preserved. The compiled connector only describes how to talk to the source. The runtime still owns pagination loops, retries, backoff, and durable cursor checkpointing. The compiler introduces no read loop of its own.

Escape hatch: manifests stay for formulaic REST sources. Anything awkward drops to a Python connector implementing the protocol directly, and a `Custom` component hook lets a manifest reference a Python callable by import path for a single odd field without abandoning the whole manifest. Both paths pass the identical conformance suite.

Schema additions to the manifest (small, backward compatible): a `paginator` block on `ListSpec` (strategy plus field names), an optional `deny` JSONPath on `AclMapping`, and the already present optional `base_url`. The Confluence example in the current docstring keeps working.

## 7. CI wiring and gating

Three distinct gates, matching pansoph's convention that permission tests are a standalone gate:

1. Hermetic conformance, every PR, blocking. A new CI job runs `run_conformance` for every connector that ships a `conformance.yaml`, under `record_mode="none"`, fully offline, no secrets. A connector PR cannot merge unless its hermetic conformance is green. This replaces today's all skipped reality.
2. Permission gate, every PR, blocking, separate checkmark. The permission category runs as its own `@pytest.mark.permission` job alongside the existing retrieval enforcement red team. The release workflow gains this as a hard gate, closing the gap where `release.yml` had no permission gate.
3. Nightly live smoke, scheduled, non blocking, fixture regenerating. A scheduled workflow runs the suite in live mode against sandbox credentials stored as CI secrets, re records cassettes and snapshots, diffs against committed fixtures, and opens a PR on drift. It never blocks merges.

Credentials for live smoke come from CI secrets and the gitignored `secrets/config.json`, never committed.

## 8. Rollout

Incremental, no big bang:

1. Land the `verify/` engine and `conformance.yaml` schema with the `InMemoryConnector` as the first passing fixture driven connector, proving the engine end to end with zero network.
2. Land the manifest compiler with a compiled version of the existing Confluence manifest as its first real exercise.
3. Un skip and fill in the existing connectors one at a time (GitHub, Google Drive, Confluence, Granola), each gaining cassettes, expected records, and permission fixtures, recorded once via live smoke against a real sandbox, then frozen. weblink gains a fixtures dir and a public only permission fixture.
4. Wire the three CI gates once the in memory connector and at least one real connector pass, so the gate is never green on nothing.

## 9. Success criteria

Evidence based, per pansoph's no fiction rule:

- `run_conformance` exists and the in memory reference connector passes all applicable categories offline.
- At least one real connector (GitHub) passes the full hermetic suite including the permission category, with committed scrubbed cassettes and fixtures.
- `compile_manifest()` no longer raises, and a compiled Confluence manifest passes the same conformance suite as a hand written connector.
- The three CI gates are wired and demonstrably block a deliberately broken ACL mapping. A regression test proves that an oversharing mapping fails the permission gate.
- Zero `@pytest.mark.skip` conformance tests remain for shipped connectors.

## 10. Out of scope

Each of these is its own later effort:

- The 20 new connectors themselves (sub-project B).
- Retrieval and RAG quality.
- The UI and UX overhaul.
- Scalability and distribution.
- Any change to the `AccessControl` shape or the authz tuple engine.

## 11. Selected libraries

Recorded here so the choice is explicit and reviewable:

- Runtime: `jsonpath-ng` for JSONPath evaluation in the manifest compiler.
- Dev and test: `vcrpy` plus `pytest-recording`, `respx`, `syrupy`.

These came out of the research as the standard 2025-2026 choices for async httpx record and replay, mocking, and snapshotting.
