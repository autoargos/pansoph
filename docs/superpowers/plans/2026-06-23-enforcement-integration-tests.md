# Enforcement Integration Tests Plan (Workstream B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the first LIVE integration tests that prove pansoph's document level ReBAC enforcement path against a real SpiceDB engine and a real pgvector store, then make them a gating CI job. The headline claim ("you only ever retrieve what you are allowed to see") becomes backed by a green LIVE checkmark, not only by in memory fakes.

**Working directory for all commands:** `/Users/yash/work/autoargos/pansoph`. Run `export PATH="$HOME/.local/bin:$PATH"` first so `uv` is found. No em dashes in any code, comment, doc, or commit message. No co-author trailer.

---

## 1. Current state vs proposed (no fiction)

### What is true today (verified 2026-06-23)

- The enforcement path is real code: `packages/pansoph-retrieval/src/pansoph_retrieval/pipeline.py:18-75` does embed, then `hybrid_search(limit=final_k*OVERFETCH)` with `OVERFETCH=4`, then `authz.bulk_check(object_ids, at_least_as_fresh=token)`, keeps the allowed subset, applies `deny` as a final filter (line 63-71, never as an authz exclusion tuple), then optional rerank.
- The `AuthzPort` surface is `write_tuples`, `delete_tuples`, `bulk_check` only (`packages/pansoph-authz/src/pansoph_authz/port.py:48-71`). No `list all accessible` call exists on the hot path. Good.
- The SpiceDB adapter is a real gRPC implementation: `packages/pansoph-authz/src/pansoph_authz/adapters/spicedb.py`. It maps writes to `WriteRelationships` (`OPERATION_TOUCH` / `OPERATION_DELETE`), maps `bulk_check` to `CheckBulkPermissions`, and sets `Consistency(at_least_as_fresh=ZedToken(token=...))` when a token is present, else `Consistency(minimize_latency=True)` (lines 153-200). It builds an async channel via `grpc.aio.insecure_channel` with a custom bearer interceptor (lines 73-107), which sidesteps the known authzed-py async InsecureClient gap (authzed/authzed-py issue #246).
- The OpenFGA adapter is real (`packages/pansoph-authz/src/pansoph_authz/adapters/openfga.py`), maps `bulk_check` to `POST /batch-check`, and accepts but ignores `at_least_as_fresh` because OpenFGA has no causal consistency token (lines 6-7, 80-112).
- The pgvector store adapter is real: `PgVectorScaleStore` in `packages/pansoph-retrieval/src/pansoph_retrieval/adapters_vector.py:45` (dense plus sparse plus RRF, ACL metadata payload, coarse pre filter).
- The markers already exist in `pyproject.toml:79-83`: `permission`, `integration`, `conformance`.

### The critical gap (the reason this plan exists)

- **Zero integration tests exist.** `uv run pytest -m integration -q` collects nothing. The full suite is 133 passed / 1 skipped in ~0.51s, all on in memory fakes (`tests/_fakes.py`: `FakeAuthz`, `FakeEmbedder`, `FakeVectorStore`).
- The permission red team (`tests/permission/test_redteam.py`, plus `tests/test_ingest_retrieve.py`) validates enforcement **logic**, not the live SpiceDB, OpenFGA, or pgvector engines. A SpiceDB schema bug, a ZedToken staleness bug, or a pgvector pre filter group leak would all ship CI green today.
- `.github/workflows/connector-live-smoke.yml:28` runs `uv run pytest -m integration -q || true`, which always passes because it collects zero tests. It is a no op gate.

### What this plan will do

Bring up a real SpiceDB and a real pgvector in CI and in local dev, seed the real schema and tuples through the real `SpiceDBAuthz` adapter, run the real `retrieve()` path end to end, and assert the invariants below. Replace the `|| true` no op with a real gating job while keeping the fast fake based suite untouched.

---

## 2. The invariants the LIVE suite proves

Each invariant maps to one or more named tests in section 4. The point is that these run against the **real engines**, so a bug in the adapter, the schema, or the token threading fails the build.

1. **Cross user isolation.** A user not in a group, and not a direct or wildcard reader, never retrieves a restricted document. The public wildcard document is still visible to everyone.
2. **Group and nested group inheritance.** A user who is a member of a parent group inherits read on a document granted to a child group. This exercises the real SpiceDB `group#member` recursion in `SCHEMA` (`spicedb.py:26-37`), which the fakes only approximate with a flat membership map.
3. **Deny as a final filter override.** An explicit `deny` for a subject (or one of their groups) removes a document even when the authz check would otherwise pass it through a group grant. The deny is applied in `pipeline.py:63-71`, never written as a SpiceDB exclusion tuple. The live test proves SpiceDB still grants (the check returns the doc) and pansoph still filters it out.
4. **Tenant isolation.** A subject in tenant `t2` never sees a `t1` document, enforced by the vector pre filter `VectorFilter(tenant_id=...)` on the real store. The authz check is tenant blind by design, so this is a real store level guarantee, not an authz one.
5. **Per connector instance isolation.** Two connector instances (`ns="inst_a"` vs `ns="inst_b"`) namespace their principals via `_ns_id` (`spicedb.py:40-43`). A `group:g1` in instance A is a different SpiceDB subject than `group:g1` in instance B. The live test proves the namespacing actually separates them in the engine, not just in Python.
6. **Pre filter is not the source of truth.** Seed a document whose vector pre filter metadata would let it pass (correct tenant, overlapping `allow_groups`) but for which **no reader tuple exists in SpiceDB**. The bulk check must exclude it. This proves the authz check, not the vector payload, decides grants. This is the single most important structural invariant and cannot be tested with fakes that derive grants from the same metadata.
7. **Revocation freshness (the headline, mapped to the Zanzibar new enemy problem).** This is the test that justifies the whole workstream. Sequence:
   1. `write_tuples(reader tuple)` returns a ZedToken `t_grant`.
   2. Index a chunk in pgvector carrying `consistency_token = t_grant` and an `acl_written_at` stamp (the fields already exist on `SearchHit`, `ports.py:28-30`).
   3. `delete_tuples(same reader tuple)` returns a fresher ZedToken `t_revoke`.
   4. Update the indexed chunk's stored token to `t_revoke` (the ingestion layer stores the freshest write token; the test does this directly).
   5. `retrieve()` threads the freshest stored token into `bulk_check(at_least_as_fresh=t_revoke)`.
   6. **Assert the revoked document is excluded.** Because `at_least_as_fresh(t_revoke)` forces SpiceDB to read no staler than the revocation, the new enemy problem cannot occur: a cached pre revocation snapshot is not allowed to satisfy the read. ([SpiceDB consistency docs](https://authzed.com/docs/spicedb/concepts/consistency)).
   7. **Negative control for non flakiness:** a second variant of the test pins `bulk_check` to the **grant** token `t_grant` after the revoke and asserts the engine MAY still return the document (bounded staleness is permitted at the older token). This proves the exclusion in step 6 is caused by the freshness threshold, not by an unrelated effect, and documents why the deterministic token threading matters.

The revocation test must be deterministic, not timing based. We never `sleep()` waiting for cache expiry. We thread the exact ZedToken returned by the revoking write, which is what `at_least_as_fresh` consumes. This is the same mechanism the production path uses (`pipeline.py:48-62`), so the test exercises the real guarantee.

---

## 3. Test architecture

### 3.1 SpiceDB bring up: `spicedb serve-testing` via testcontainers

Verified recommendation from Authzed: use `spicedb serve-testing`, which gives **each preshared key its own isolated, ephemeral in memory (memdb) datastore**, so tests using distinct tokens run in parallel without cross contamination ([Authzed datastores docs](https://authzed.com/docs/spicedb/concepts/datastores), [Authzed testcontainers + RAG guide](https://authzed.com/docs/spicedb/integrations/testcontainers)). Two supported ways to start it:

- **Local and CI default: testcontainers-python with the official `testcontainers-spicedb` module.** `SpiceDBContainer(image="authzed/spicedb:v1.47.1")` (the version Authzed pins in its own RAG testing guide), then `container.get_endpoint()` and `container.get_secret_key()`. Container ports are mapped dynamically, health checked, and torn down automatically. This is a **dev/test only** dependency.
- **CI alternative: a GitHub Actions service container** running `authzed/spicedb:v1.47.1` with `--entrypoint spicedb ... serve-testing` on a fixed port. We pick **testcontainers** as the primary path (no fixed port collisions, same code locally and in CI, parallel safe), and document the service container form as the fallback in `connector-live-smoke.yml` comments.

Manual local invocation, for debugging, matches the Authzed docs:

```bash
docker run --rm -p 50051:50051 -p 50052:50052 \
  --entrypoint spicedb authzed/spicedb:v1.47.1 serve-testing
```

### 3.2 pgvector bring up: testcontainers PostgresContainer with the pgvector image

```python
from testcontainers.postgres import PostgresContainer

with PostgresContainer("pgvector/pgvector:pg17", driver=None) as pg:
    dsn = pg.get_connection_url()  # async driver added by the fixture
```

We use `pgvector/pgvector:pg17` to match the ADR-0002 Postgres 17 target (`CLAUDE.md` section 4). `driver=None` keeps the URL driver agnostic so the fixture can hand it to the async driver the `PgVectorScaleStore` uses. The fixture runs `CREATE EXTENSION IF NOT EXISTS vector;` and applies the store's schema before yielding. pgvectorscale is not required for these tests (plain pgvector covers the enforcement assertions); if the store hard requires the `vectorscale` extension, switch the image to `timescale/timescaledb-ha:pg17` and create both extensions. Resolve this when wiring Task 4 (see open question).

### 3.3 Markers, fixtures, and seeding

- **Marker:** every test in this suite is `@pytest.mark.integration`. The existing fast suite runs `-m "not integration"` and is unaffected.
- **`tests/integration/conftest.py`** holds session scoped fixtures:
  - `spicedb_endpoint` and `spicedb_key` from the SpiceDB container.
  - `pg_dsn` from the Postgres container, extension created, schema applied.
  - A `skip_if_no_docker` guard that `pytest.skip(...)`s the whole module when Docker is unavailable, so a developer without Docker still gets a green `-m "not integration"` run and a clear skip reason on the integration run.
- **Per test isolation for SpiceDB:** each test (or test class) uses a **unique preshared key** so `serve-testing` allocates a fresh empty datastore. The simplest deterministic scheme is `key = f"test-{request.node.name}"`. This removes the need to delete tuples between tests and makes parallel runs safe.
- **Seeding the schema:** call `SpiceDBAuthz(endpoint, key).ensure_schema()` once per datastore. The real `SCHEMA` (`spicedb.py:26-37`) is what gets loaded, so a schema regression is caught here.
- **Seeding tuples:** build `pansoph_authz.Tuple` objects and call the real `SpiceDBAuthz.write_tuples(...)`. Capture the returned `ConsistencyToken`. This is the same call the ingestion ACL mappers make.
- **Seeding the vector store:** construct real `SearchHit` rows with `tenant_id`, `allow_groups`, `deny`, `consistency_token`, and `acl_written_at`, embed with a tiny deterministic fake embedder (the embedder is not under test here; the authz and store are), and `upsert(...)` into `PgVectorScaleStore`. Using a fake embedder is allowed: this suite proves enforcement, not retrieval quality, and keeping it model free keeps CI fast and hermetic. The vector store and the authz engine are the real components.
- **Threading the ZedToken deterministically:** the helper that seeds a chunk stores on the `SearchHit` the exact token returned by the tuple write that granted (or revoked) it. `retrieve()` then picks `max(candidates, key=acl_written_at)` and passes that token to `bulk_check` (`pipeline.py:48-62`). The revocation test sets `acl_written_at` so the revoke chunk's token dominates. No timing, no sleeps.

### 3.4 Helper shape (illustrative, lands in `tests/integration/_live.py`)

```python
async def seed_chunk(
    store, authz, *, doc_id, tenant_id, allow_groups, deny, reader_tuples
):
    token = await authz.write_tuples(reader_tuples)  # real SpiceDBAuthz
    hit = SearchHit(
        doc_id=doc_id,
        chunk_id=f"{doc_id}#0",
        score=1.0,
        tenant_id=tenant_id,
        allow_groups=allow_groups,
        deny=deny,
        consistency_token=str(token),
        acl_written_at=_monotonic_stamp(),
    )
    await store.upsert([hit], [await _embed(hit.text or doc_id)])
    return token
```

---

## 4. The first integration tests (named, with assertions)

All land in `tests/integration/test_enforcement_live.py`, all `@pytest.mark.integration`, all wired to a real `SpiceDBAuthz` plus a real `PgVectorScaleStore`. Run via `retrieve(...)` so the full path (pre filter, overfetch, bulk check, deny filter) is under test.

- [ ] **`test_live_cross_user_isolation_excludes_non_member`**
  Seed an `eng` doc readable by `group:g1` and a `pub` doc readable by `user:*`. Assert alice (member of g1) gets both, bob (no groups) gets only `pub`, and `eng` is absent for bob. Proves invariant 1 against the real engine.

- [ ] **`test_live_nested_group_inheritance_grants_parent_member`**
  Write `group:parent#member` includes `group:child#member`, grant `eng` to `group:child`, make alice a member of `parent`. Assert alice retrieves `eng` through two levels of `group#member` recursion. Proves invariant 2; this is the case the flat fake cannot model.

- [ ] **`test_live_deny_overrides_group_grant`**
  Grant `secret` to `group:g1`, deny `alice` on the chunk's `deny` list, leave carol (also g1) undenied. Assert SpiceDB returns `secret` for alice (the check passes) but `retrieve()` excludes it for alice and includes it for carol. Proves invariant 3 and that deny is a pipeline filter, not an authz tuple.

- [ ] **`test_live_tenant_isolation_blocks_cross_tenant`**
  Seed a `t1` doc and a `t2` doc, both public. Assert a subject querying with `tenant_id="t2"` gets only the `t2` doc. Proves invariant 4 at the real store pre filter.

- [ ] **`test_live_per_connector_instance_namespacing`**
  Seed `group:g1` granting a doc in instance `inst_a` and a different doc in instance `inst_b`, with principals namespaced via `Principal(ns=...)`. Assert a subject resolved in `inst_a`'s namespace sees only `inst_a`'s doc. Proves invariant 5 (`_ns_id` separation in the real engine).

- [ ] **`test_live_prefilter_is_not_source_of_truth`**
  Seed a chunk with `tenant_id` and `allow_groups=["g1"]` so it passes the vector pre filter for a g1 subject, but write **no reader tuple** for it in SpiceDB. Assert `retrieve()` excludes it. Proves invariant 6: the bulk check, not the vector payload, is authoritative.

- [ ] **`test_live_revocation_freshness_excludes_after_revoke`** (HEADLINE)
  Grant `eng` to `group:g1` (token `t_grant`), index the chunk, then `delete_tuples` the grant (token `t_revoke`), update the chunk's stored token to `t_revoke`. Assert `retrieve()` (which calls `bulk_check(at_least_as_fresh=t_revoke)`) excludes `eng` while an unrelated public doc remains. Proves invariant 7, the Zanzibar new enemy guarantee, deterministically via the ZedToken.

- [ ] **`test_live_revocation_stale_token_may_still_see` (negative control)**
  Same setup, but after the revoke pin `bulk_check` to `t_grant`. Assert the engine is permitted to still return `eng` (bounded staleness at the older token). This documents that the exclusion in the headline test is caused by the freshness threshold, and guards against a false positive where some other bug excludes the doc regardless of token.

- [ ] **`test_live_schema_loads_and_wildcard_resolves`**
  Call `ensure_schema()` then grant `user:*` on a doc and assert an arbitrary user retrieves it. A fast smoke that fails loudly if the real `SCHEMA` string stops compiling against the pinned SpiceDB version.

- [ ] **`test_live_bulk_check_empty_object_ids_returns_empty`**
  Drive `retrieve()` with a query that pre filters to zero candidates and assert no SpiceDB call is made and the result is empty (guards the `if not candidates: return []` and `if not object_ids: return set()` short circuits, `pipeline.py:45` and `spicedb.py:170-171`).

Tests 1 to 7 are the gating core. Tests 8 to 10 are cheap hardening that share the same fixtures.

---

## 5. CI change: real gating job, fast suite untouched

### 5.1 Add a gating `enforcement-live` job to `.github/workflows/ci.yml`

Add a new job (do not modify `lint-type-test`, `permission-gate`, or `conformance-gate`). Use GitHub Actions service containers so the engines are up before pytest runs, and have the SpiceDB adapter point at the service. Because testcontainers also works in CI, an equally valid form is to let testcontainers start the containers inside the job; pick **one** and keep the local and CI paths identical. The service container form:

```yaml
  enforcement-live:
    runs-on: ubuntu-latest
    services:
      spicedb:
        image: authzed/spicedb:v1.47.1
        ports:
          - 50051:50051
        # serve-testing gives each preshared key an isolated ephemeral datastore.
        options: >-
          --entrypoint spicedb
        env:
          SPICEDB_GRPC_PRESHARED_KEY: integration-test
      postgres:
        image: pgvector/pgvector:pg17
        ports:
          - 5432:5432
        env:
          POSTGRES_PASSWORD: pansoph
          POSTGRES_DB: pansoph
        options: >-
          --health-cmd "pg_isready -U postgres" --health-interval 5s
          --health-timeout 5s --health-retries 10
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          version: "0.5.x"
          enable-cache: true
      - run: uv python install 3.12
      - run: uv sync --all-packages --dev
      - name: Enforcement integration tests (live SpiceDB + pgvector)
        env:
          PANSOPH_SPICEDB_ENDPOINT: localhost:50051
          PANSOPH_SPICEDB_KEY: integration-test
          PANSOPH_PG_DSN: postgresql://postgres:pansoph@localhost:5432/pansoph
        run: uv run pytest -m integration -q
```

> Note on the service `command`/`serve-testing`: GitHub service containers cannot pass a subcommand cleanly through `options`. If `serve-testing` cannot be set this way for the pinned image, use **testcontainers inside the step** instead (start `SpiceDBContainer` and `PostgresContainer` in `conftest.py`, drop the `services:` block, keep the same `pytest -m integration`). The testcontainers path is the recommended one precisely because it owns `serve-testing` and dynamic ports. Whichever is chosen, the job is **gating** (no `|| true`).

This job is **separate** so a green checkmark on `enforcement-live` reads, on its own, as "permissions are enforced against the real engine," mirroring how `permission-gate` already stands alone.

### 5.2 Fix the no op in `connector-live-smoke.yml`

The scheduled live smoke at `.github/workflows/connector-live-smoke.yml:28` is `uv run pytest -m integration -q || true`. That now collides with the new gating job's purpose. Two correct moves:

- [ ] Narrow the live smoke to connector smoke only with a distinct marker (for example `-m "integration and connector_smoke"`), keeping it non blocking and credential driven, OR
- [ ] Remove the `-m integration` line from the smoke workflow entirely and let `connector-live-smoke` do only fixture drift re recording, since enforcement integration now has its own gating job.

Either way, the `|| true` no op that "always passes" must not remain as the project's only integration signal. Pick the narrower marker approach so the two workflows do not overlap (open question below).

### 5.3 Dependencies (dev/test only, no ADR required)

Add to the root `pyproject.toml` `dev` dependency group:

```toml
"testcontainers[postgres]>=4.14",
"testcontainers-spicedb>=0.0.1",
"authzed>=1.24",
"grpcio>=1.66",
"asyncpg>=0.29",  # or the async driver PgVectorScaleStore already uses; reuse it
```

`authzed` and `grpcio` are already implied by the SpiceDB adapter's optional extra (`pansoph-authz[spicedb]`); the integration suite makes them dev dependencies of the workspace too. **Call out:** testcontainers, the SpiceDB image, and the authzed client here are **development and test dependencies**, not runtime heavy dependencies. `CLAUDE.md` section 4 requires an ADR for a new **datastore, framework, or broker added to the runtime**. None of these is added to the runtime; they only run under `pytest -m integration`. No ADR is required. If a reviewer disagrees, the lightweight path is a one paragraph note in the PR rather than a full ADR.

---

## 6. OpenFGA adapter path parity

The `OpenFGAAuthz` adapter (`adapters/openfga.py`) is the first class alternate and shares the exact `AuthzPort` surface, so the same test bodies can run against it. Two facts shape the decision:

1. **OpenFGA has no causal consistency token.** The adapter accepts `at_least_as_fresh` and ignores it (lines 6-7, 85). The revocation freshness invariant (the headline) therefore **cannot** be proven against OpenFGA the same way. OpenFGA's only knob is an opt in `HIGHER_CONSISTENCY` flag, not a token, so its revocation semantics differ and would need a different (weaker) assertion.
2. **There is a known 2025-2026 BatchCheck regression and CVE** noted in the adapter docstring (line 13). Live testing OpenFGA is valuable precisely to catch that, but it needs a pinned, load tested server.

**Recommendation:** Parameterize the **non freshness** tests (cross user, nested group, deny, namespacing, pre filter not source of truth) over both engines using a pytest fixture param (`["spicedb", "openfga"]`), bringing up OpenFGA via its own testcontainer when the param is `openfga`. Keep the **revocation freshness** test SpiceDB only, with an explicit `xfail`/skip and a comment for OpenFGA stating that token based freshness is a SpiceDB guarantee. This is honest (no fiction) and still gives OpenFGA real coverage on the invariants it can satisfy.

**Scope for this workstream:** land SpiceDB live tests first (all 10), get the gating job green, then add the OpenFGA parameterization as a fast follow. Do not block the SpiceDB gate on OpenFGA server hardening.

---

## 7. Acceptance criteria and definition of done

- [ ] `tests/integration/conftest.py` brings up a real SpiceDB (`serve-testing`) and a real pgvector, with a Docker guard that skips cleanly when Docker is absent.
- [ ] All 10 named tests in section 4 exist, are `@pytest.mark.integration`, and are wired to `SpiceDBAuthz` plus `PgVectorScaleStore` (no fakes for authz or vector store).
- [ ] `uv run pytest -m integration -q` collects and passes **10+** tests locally with Docker running (today it collects 0).
- [ ] The revocation freshness test passes deterministically across **3 consecutive runs** with no sleeps and no flakiness (record the run in `docs/runs/` or the PR description per the no fiction rule).
- [ ] `uv run pytest -m "not integration" -q` still passes in ~0.5s and is byte for byte unaffected (the fast fake suite is untouched).
- [ ] `ci.yml` has a new **gating** `enforcement-live` job; a failing enforcement test fails the build.
- [ ] `connector-live-smoke.yml` no longer relies on `pytest -m integration -q || true` as a meaningful signal (narrowed marker or removed).
- [ ] `uv run ruff check . && uv run ruff format --check . && uv run mypy packages` pass for the new test and fixture code.
- [ ] No em dashes anywhere in the new files. Conventional commit, no co-author trailer.
- [ ] The README / claim that "you only retrieve what you are allowed to see" is now backed by a **green LIVE checkmark** on `enforcement-live`, not only by in memory fakes. Update any doc that implied live proof existed before it did.

**Done means:** a reviewer can click the `enforcement-live` check on a PR, see it green, and trust that cross user isolation, nested group inheritance, deny override, tenant and per instance isolation, the pre filter is not source of truth invariant, and revocation freshness all held against a real SpiceDB and a real pgvector.

---

## 8. Task checklist (execution order)

- [ ] **Task 1.** Add dev/test deps (section 5.3). Confirm `uv sync --all-packages --dev` resolves.
- [ ] **Task 2.** Create `tests/integration/conftest.py` with `spicedb_*`, `pg_dsn`, and Docker guard fixtures.
- [ ] **Task 3.** Create `tests/integration/_live.py` seed helpers (`seed_chunk`, embedder shim, monotonic stamp).
- [ ] **Task 4.** Resolve the pgvector vs pgvectorscale image question; apply the store schema in the fixture (open question 1).
- [ ] **Task 5.** Write tests 1 to 6 (the non freshness invariants) against SpiceDB + pgvector. Get them green locally.
- [ ] **Task 6.** Write tests 7 and 8 (revocation freshness headline + negative control). Verify 3 clean runs.
- [ ] **Task 7.** Write tests 9 and 10 (schema smoke, empty short circuit).
- [ ] **Task 8.** Add the gating `enforcement-live` job to `ci.yml`; resolve service container vs testcontainers form (open question 2).
- [ ] **Task 9.** Narrow or remove the `pytest -m integration -q || true` line in `connector-live-smoke.yml`.
- [ ] **Task 10.** Parameterize tests 1 to 5 over `["spicedb", "openfga"]` as a fast follow (section 6).
- [ ] **Task 11.** Update docs to claim live proof only now that the green check exists.

---

## 9. Open questions for the maintainer

1. **pgvector vs pgvectorscale image.** Does `PgVectorScaleStore` hard require the `vectorscale` extension, or does plain `pgvector/pgvector:pg17` satisfy the enforcement tests? If it requires vectorscale, the fixture image becomes `timescale/timescaledb-ha:pg17` and the fixture creates both extensions.
2. **CI bring up form.** Service containers (cleaner YAML, but `serve-testing` subcommand is awkward to pass) vs testcontainers inside the step (owns `serve-testing`, dynamic ports, identical to local). Recommendation: testcontainers. Confirm.
3. **OpenFGA in this workstream or a fast follow.** Recommendation: SpiceDB first, OpenFGA parameterization as a follow up, given the BatchCheck CVE needs a pinned, load tested server.
4. **Live smoke overlap.** Narrow `connector-live-smoke` to a `connector_smoke` sub marker, or strip integration from it entirely. Recommendation: narrow.

---

## Sources (verified 2026-06-23)

- SpiceDB consistency model, the four modes (`minimize_latency`, `at_least_as_fresh`, `at_exact_snapshot`, `fully_consistent`), ZedToken, and the new enemy problem: https://authzed.com/docs/spicedb/concepts/consistency
- ZedTokens and Zookies, storing the token alongside content, fresher tokens are always safe: https://authzed.com/docs/reference/zedtokens-and-zookies
- `spicedb serve-testing`, isolated ephemeral datastore per preshared key, memdb for integration testing: https://authzed.com/docs/spicedb/concepts/datastores
- Testing RAG pipelines with Testcontainers and SpiceDB, image `authzed/spicedb:v1.47.1`, `get_endpoint()` / `get_secret_key()`, dynamic port mapping: https://authzed.com/docs/spicedb/integrations/testcontainers
- SpiceDB validation, testing, and debugging guidance (test relations, `zed validate`, integration test server): https://authzed.com/docs/spicedb/modeling/validation-testing-debugging
- GitHub Action for SpiceDB integration testing (`serve-testing`): https://github.com/authzed/action-spicedb
- `CheckBulkPermissions` guidance and pinning calls to a single ZedToken revision: https://authzed.com/docs/spicedb/concepts/commands
- Consistency is the Key to Performance and Safety (quantization, conditional caching, why at_least_as_fresh over fully_consistent): https://authzed.com/blog/consistency-is-the-key-to-performance-and-safety
- testcontainers-python PostgresContainer with the pgvector image and `driver=None` async pattern: https://testcontainers.com/modules/pgvector/
- testcontainers-python project (latest 4.14.x): https://github.com/testcontainers/testcontainers-python
- authzed-py async InsecureClient limitation (issue #246), relevant to why the adapter builds its own `grpc.aio` channel: https://github.com/authzed/authzed-py/issues/246
