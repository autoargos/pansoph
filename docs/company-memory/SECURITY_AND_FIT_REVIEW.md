# Pansoph security and fit review for DOOR3 Company Memory

Reviewed: 2026-09-29, at fork commit `073aae6` (upstream `autoargos/pansoph`, merged through PR 2).
Scope: the whole repository, read from code and tests rather than from documentation.
Method: every execution path from the request to the response, the LLM, or the MCP tool was
traced by hand. Four connectors and the deployment and supply chain surface were reviewed in
depth. The hermetic suite and dependency audits were run, and route level permission tests
were added (`tests/permission/test_company_memory_*.py`).

Gap ids (`CM-B1`, `CM-H1`, ...) are stable. Where a gap has a reproduction, it is pinned as a
strict expected failure in `tests/permission/test_company_memory_known_gaps.py`.

## Status update, 2026-09-29 (demo phase)

The local synthetic demo work (see `DEMO.md`) closed or mitigated these gaps. The evidence
is the tests named here, plus `company-memory verify` passing against the live stack
(real Keycloak, SpiceDB, and pgvector).

| Gap | Status | How | Evidence |
|---|---|---|---|
| CM-B1 | **Closed for the Company Memory deployment**. The upstream default is unchanged. | `PANSOPH_REQUIRE_AUTH=true`, the OIDC adapter, and a startup guard that refuses to start otherwise. | `test_require_auth_mode_*`, `test_startup_guard_refuses_unsafe_config`. The strict xfail for the *default* config stays open. |
| CM-B2 | **Closed for the local POC stack** (`deploy/company-memory/`). Upstream `compose.yaml` is unchanged. | Three loopback ports only, generated secrets required by compose, no demo realm, and a guard against default values. | `test_compose_*`, `test_secrets_*`, `test_realm_template_*`. |
| CM-H1 | **Fixed** (core). | `memberships.reconcile_memberships` deletes stale member tuples before the check, and the OIDC adapter can read groups from userinfo per request. | `test_cm_h1_*`, `test_revoked_group_membership_*` (both run in authz only mode as well). |
| CM-H2 | **Fixed** (core). | A document that yields no chunks is purged (its chunks and tuples). | `test_cm_h2_*`. |
| CM-H3 | **Fixed** (core). | A document's chunks are deleted before re-upserting it. | `test_cm_h3_*`. |
| CM-H5 | **Fixed** (core). | Deleting an instance purges its chunks, tuples, ACL records, and sealed secrets. | `test_purging_an_instance_*` (hermetic; not exercised live by the demo). |
| CM-H6 | **Fixed** (core and fork). | Bearer auth at the MCP transport (the SDK's resource server middleware plus `IdentityTokenVerifier`). The principal comes from the request context. No tool has a credential parameter, upstream `pansoph-mcp` included. MCP tools are audited. | `test_unauthenticated_or_invalid_mcp_request_is_rejected`, `test_no_tool_schema_has_a_credential_parameter`, `test_mcp_and_rest_*`. |
| CM-H4 | **Mitigated** in the Company Memory deployment. The upstream route is unchanged. | The `/api/grab` route is not mounted (`company_memory_mcp.agora_app`). | `test_agora_wrapper_drops_grab_and_docs`. The strict xfail stays open. |
| CM-M3 | **Partially fixed.** | `exp`, `iss`, `aud` and `sub` are required. An unverified email is not used as the subject. Groups from userinfo fail closed. The `preferred_username` fall back remains. | `test_token_without_expiry_*`, `test_unverified_email_*`, `test_failed_or_mismatched_userinfo_denies`. |
| CM-M8 | **Mitigated for the POC.** | Audit events go to stdout, viewed with `company-memory audit`. This is still not an immutable store. | `test_audit_records_*`. |
| CM-H10 | **Partially. Also corrected: the first audit under-counted.** | The fork image builds strictly `--frozen` with uv pinned to 0.11.27. Dependencies are not bumped. The first audit exported the workspace without the `pansoph-core[stack]` extra, which the agora images install, so it missed that extra's dependencies. With `stack` included, `pip-audit` finds 41 advisories in 8 packages, all at the same locked versions as before: aiohttp 3.14.0 (fix 3.14.3, 22 advisories, via aioboto3), h2 4.3.0 (fix 4.4.1), hpack 4.1.0 (fix 4.2.0), plus the five packages reported earlier. | `test_secrets_and_state_*`, and the `uvx pip-audit` run on 2026-09-29. |

This phase also found a new issue, now fixed. The bulk check used the candidates'
chunk consistency token, which is older than the request's own membership write. SpiceDB
could therefore serve a snapshot from before the user's memberships existed. The first
query after a membership change then under-shared: it failed closed, but it was wrong.
`retrieve()` now takes `at_least_as_fresh`, and the query path passes its membership write
token, so both new grants and revocations are read. The live `verify` caught this.

### Manual knowledge through MCP (2026-09-29)

A new capability, not a gap fix: `add_company_memory_knowledge` (plus list and delete)
lets a signed in user add knowledge through MCP. It adds a write surface, so its
properties are recorded here.

- The creator and tenant come from the verified token. The ACL comes from the server:
  private to the creator by default, or a group scope only with an explicit publish grant
  (`publish-policy.json`, a `*-publishers` IdP group plus membership of the scope).
  Reading a group never grants publishing, and tenant wide scopes are refused.
- Ingestion is the ordinary `IngestionPipeline.ingest_one` over a manual source record,
  so tuples and the ACL log are written the normal way. Reads use the unchanged
  `retrieve()` and `get_source` checks. No second retrieval path.
- The server takes text, never a path or URL; the parser's text path does no fetching or
  deserialization. Content is capped at 200 KB and request bodies at 1 MB.
- Writes are audited (actor, tenant, tool, decision, document id, requested scope,
  effective ACL, content size and hash prefix), never the content.
- Evidence: `tests/permission/test_company_memory_knowledge.py` (29 tests, mutation
  checked), and the knowledge section of `company-memory verify` against the live stack.
- Residual risks: prompt injection in stored text reaches readers' agents (it is labelled
  `user_supplied`, not filtered); a publisher can add misleading content with no review
  step; the MCP process now holds SpiceDB and pgvector write access; no admin delete.

### Real DOOR3 identities, local POC (2026-09-29)

Real identities, synthetic knowledge only. What changed and why it holds:

- **Principal:** the `company_memory` identity adapter wraps OIDC with
  `require_verified_email` (a new opt in on the OIDC adapter): signature, issuer, audience,
  expiry, subject, and `email_verified: true` are all required, and the principal is the
  lowercased email. CM-M3 is now closed for the Company Memory deployment (the
  `preferred_username` fall back can no longer become a principal there).
- **Configured users only:** `CM_REQUIRED_GROUPS=everyone,cm-service-accounts`. A validly
  signed token for an account outside those groups is a 401 on MCP and REST. `everyone`
  is an explicit directory group, never the implicit tenant group, never anonymous.
- **Tenant:** real users carry an admin only Keycloak `tenant` attribute (`door3`) mapped
  into the token; the synthetic users stay in `default`. Tenants share nothing.
- **`it-support`:** read groups `everyone`, publisher group `it-support-publishers`. The
  policy refuses `everyone` as a publisher group and any publisher group that is also a
  reader, so reading never grants publishing.
- **Secrets:** real emails live in the gitignored `users.local.json`; generated passwords
  in `.secrets/`. The committed example uses `example.com` placeholders, and the tests
  never use real identities.
- Evidence: `tests/permission/test_company_memory_real_users.py`, the identity tests in
  `tests/security/test_company_memory_hardening.py` (both mutation checked), and
  `company-memory verify-real` against the live stack.
- Residual: the CLI logs users in with the OAuth password grant and a generated local
  password (POC only); Keycloak runs in dev mode over HTTP on loopback; the rendered
  realm file (0644 inside a 0700 directory) holds the generated passwords.

The gaps still open and relevant to the demo boundary are CM-H7, CM-H8, CM-H9, CM-H10,
CM-M1, CM-M2, CM-M4, CM-M5 and CM-M6. The demo's own limitations are listed in `DEMO.md` and in
the fork patch register.

---

## Executive summary

Pansoph is a **viable foundation, but not in its current state**. Its core retrieval design is
right for Company Memory:

- permission enforcement happens at retrieval, one bulk check against a real ReBAC engine
  (SpiceDB), before anything reaches the LLM or the caller;
- an ACL diff on re sync revokes grants that were removed first;
- a thin port based architecture lets DOOR3 wrap it rather than fork it.

Hermetic tests cover the retrieval pipeline well, and in this review it held up under every
direct test.

The weaknesses sit around that core, and several are **blockers for ingesting real DOOR3
data**:

1. **Anyone can claim any identity.** The search and chat API honors an identity (user,
   groups, tenant) supplied in the request body whenever the bearer token is missing or
   invalid. With the default config, anyone who can reach agora can read anything by claiming
   to be someone else (CM-B1).
2. **The default deployment gives away full authz write access.** SpiceDB is published on the
   host with a default preshared key (CM-B2).
3. **Revocation is incomplete in several real cases.**
   - Group membership tuples are never removed (CM-H1).
   - A revocation is skipped when the new version of a document parses to nothing (CM-H2).
   - Removed text survives in trailing chunks (CM-H3).
   - Deleting a connector instance purges nothing (CM-H5).
   - GitHub does not re check permissions on unchanged files (CM-H8).
4. **Identity mapping between sources and users is mostly missing.** Only email grants resolve.
   Slack, GitHub and Jira user grants resolve to nobody. Identity groups and connector groups
   share one namespace (CM-M1).
5. **The MCP server passes the user's bearer token as a tool argument.** It has no transport
   authentication and no audit trail (CM-H6).

None of these require redesigning Pansoph. Most are small, local fixes, and several are good
upstream contributions. **Recommendation: suitable with conditions** (see the end of this
document). Use a local POC with synthetic data now. Real DOOR3 data should wait until the
blockers are closed.

---

## Architecture observed

What exists in code (not docs):

| Concern | Where | Notes |
|---|---|---|
| API entry points | `packages/pansoph-core/src/pansoph_core/routes.py`, `routes_resources.py`, `routes_group_aliases.py`, `app.py` | FastAPI. `/api/search`, `/api/chat` (SSE), `/api/grab`, connector instance CRUD and sync, OAuth connect, group alias admin, discovery, health, meta. `/docs` and `/openapi.json` are on. |
| MCP server | `pansoph_core/mcp_server.py` | One tool, `pansoph_search(query, token)`, over FastMCP streamable HTTP, run as a separate `pansoph-mcp` process. It is not in compose or Helm. |
| MCP bridge (inbound) | `packages/pansoph-mcp-bridge/.../bridge.py` | A connector that reads resources from a remote MCP server. The ACL is set by the operator in config. |
| Retrieval pipeline | `packages/pansoph-retrieval/.../pipeline.py` | embed, hybrid search with a tenant and group pre filter, overfetch x4, one `bulk_check`, a deny filter, a tenant re check, then an optional rerank. |
| Vector stores | `.../adapters_vector.py` | pgvector (a dense query and a tsvector query fused with RRF k=60) and Qdrant (dense only, so no hybrid). |
| Authz | `packages/pansoph-authz` | `AuthzPort`, SpiceDB (gRPC, CheckBulkPermissions, ZedToken), OpenFGA (REST batch-check, no consistency token), and `none` (allow all). Mappers turn an ACL into tuples. |
| ACL model | `pansoph_types.AccessControl` | `public`, `allow_groups`, `allow_users`, `deny`, `principal_ns`. Users and groups are namespaced by **tenant** (`mappers.py:47`), not by connector instance. |
| Identity | `packages/pansoph-identity` | `builtin` (the default: an in memory, empty store per request, where the token is a user id) and `oidc` (PyJWT against JWKS, iss and aud checked). |
| Ingestion | `packages/pansoph-ingestion/.../pipeline.py` | fetch, narrow by instance scope, parse, chunk, embed, reconcile the ACL through `AclLog` (Postgres `pansoph_doc_acls`), then upsert chunks carrying their consistency token. |
| Parsing | `.../adapters_parser.py` | A text fast path, an stdlib HTML strip, and Docling for binaries (an optional extra, run in process with no size limit). |
| Workers | `pansoph_core/app.py` lifespan, `scheduler.py`, `connectors.py` | **Syncs run inside the agora API process** as asyncio tasks. The `scribe` worker image exists. The DBOS wrapper (`workflow_dbos.py`) exists but is not the default. |
| Database | Postgres tables created with ad hoc `CREATE TABLE IF NOT EXISTS` at first use | `pansoph_chunks`, `pansoph_doc_acls`, `connector_instances`, `sync_runs`, `connector_secrets`, `oauth_states`, `pansoph_group_aliases`. No migrations, and no tenant column on instances used as a predicate. |
| Caching | none on the retrieval path | Valkey is deployed but unused by Python code. The only caches are the OIDC per process user cache, the SpiceDB last token, and the connector SDK token cache. **There is no result cache that could leak stale access.** |
| Secrets | `pansoph_core/secrets.py`, `pansoph-infra` envelope and OpenBao adapters | Connector secret fields are sealed into the SecretStore and stored as `{"_secret": name}` handles. |
| Tenant isolation | the vector pre filter, a post filter `hit.tenant_id == tenant_id`, and tenant namespaced tuples | There is no RLS in practice: `deploy/security/rls.sql` is never applied. Admin routes take the tenant from the request body. |
| Audit | `pansoph_core/audit.py` | JSON lines on the `pansoph.audit` logger. No handler is configured anywhere in the repo, so under Python defaults these INFO records are discarded. MCP searches are not audited. |
| UI | `apps/lens` | React. It sends a demo identity (user, groups, tenant) in the body of every search and chat request, alongside the token. |

### Code vs tests vs docs vs plans

| Capability | In code | Automated test | Docs only | Planned |
|---|---|---|---|---|
| Overfetch, then bulk check, then filter | yes | yes (hermetic red team, plus the live suite: 9 passed on 2026-09-29 against SpiceDB v1.47.1 and pgvector pg17) | | |
| Revocation by ACL diff on re sync | yes | yes (`test_revocation_deletion.py`, plus route level tests added here) | | |
| Consistency token threading | yes (per chunk token) | yes (`test_token_freshness.py`) | | |
| Tenant bound to token | yes (OIDC `tenant` claim) | yes (unit tests, plus an end to end test added here) | | |
| Group alias bridge | yes | yes | | |
| MCP server parity with REST | yes | **added in this review** (`test_mcp_enforces_exactly_the_rest_authorization`) | | |
| Deny as final filter | yes | yes | | |
| Webhook driven sync | connectors implement `on_webhook` | conformance only | "real time" in docs | no route calls it |
| `list_acls` permission only resync | connectors implement it | conformance only | ADR | never called at runtime |
| Sandbox for connector and MCP code (gVisor, Firecracker) | **no** | no | CLAUDE.md s8, ADR-0002 | yes |
| Egress allowlist | squid in `compose.hardened.yaml`, scribe only, advisory env var | no | CLAUDE.md s8 | |
| Postgres RLS FORCE | a SQL file exists, **never applied** | no | CLAUDE.md, ADR-0002 | |
| Immutable audit log | a logger with no sink | no | CLAUDE.md s8 | |
| SCIM, source principal linking | no | no | | yes |
| Durable ingestion (DBOS) | an optional wrapper | no | | default later |

---

## Security model

### Authentication

- **Bearer token to Identity port.** `_identity_from_token` (`routes.py:73`) calls
  `build_identity().authenticate(token)`. **Any exception is swallowed and treated as
  anonymous** (`routes.py:79`). An IdP or JWKS outage is therefore indistinguishable from "no
  token".
- **OIDC adapter.** It verifies RS256 and ES256 signatures against JWKS and checks `iss` and
  `aud=client_id`. `exp` is verified when present but not required.
  - It maps `email`, `preferred_username` or `sub`, `groups`, `roles` and `tenant` claims.
  - It does **not** check `email_verified`, yet the email is the authorization subject.
  - With no email claim, the subject becomes `preferred_username`, which many IdPs let users
    change.
- **The builtin adapter (the default in `.env.example` and `settings.py`)** is built fresh and
  empty on every request, so `authenticate` always returns `None`. **With the default config
  every request is anonymous**, and the admin routes are unreachable.
- **The body identity fall back (CM-B1).** `_effective_identity` (`routes.py:92`) returns
  `req.user, req.groups, req.tenant_id` from the JSON body when no user was authenticated.
  This is the demo switcher in lens, but it is live on every deployment.
  - **Mitigation added in this review:** `PANSOPH_REQUIRE_AUTH=true` turns the fall back into
    a 401. It is off by default, so upstream behavior is unchanged.
- **Management roles.** A flat `roles` claim gates `admin` and `connector_admin`
  (`require_role`).
- **Unauthenticated routes:**
  - `GET /api/connectors/instances` returns every tenant's instances, including their config,
    scope, and last errors.
  - `GET /api/connectors/instances/{id}/runs`.
  - `/api/meta`, `/api/adapters`, `/api/health`, `/docs`.

### Authorization flow (traced)

```
POST /api/search | /api/chat | MCP pansoph_search
  -> identity: token (OIDC) | body fall back (REST only, CM-B1)
  -> subject = email or id, groups = token groups, tenant = token tenant claim or "default"
  -> expand_groups(groups, aliases[tenant])           (routes.py:167)
  -> authz.write_tuples(member tuples for the effective groups)   (never deleted: CM-H1)
  -> retrieve():
       embed(query)
       vector.hybrid_search(tenant = T AND (no groups OR groups overlap the effective groups),
                            limit = final_k * 4)
       authz.bulk_check(subject, "read", candidate doc ids, at_least_as_fresh = freshest token)
       keep hits where: doc_id allowed AND hit.tenant == T AND subject and groups not in deny
       rerank(allowed)   (the reranker only ever sees allowed hits)
  -> response: doc_id, chunk_id, score, text   |   SSE sources, then LLM(prompt of allowed text)
```

Properties verified:

- **Default deny at the engine.** `bulk_check` returns only ids with `HAS_PERMISSION`.
  Conditional or unknown results are dropped. A SpiceDB error raises and becomes a 500, which
  fails closed.
- **No path from search, chat, or MCP to content that skips `retrieve()`**, and `retrieve()`
  always calls `bulk_check` when it has candidates.
- **The admin only content reads bypass authz by design.**
  `GET /api/connectors/instances/{id}/documents` returns a 200 character snippet of every
  indexed document of an instance to any `connector_admin`, for any tenant (the tenant is
  taken from the instance row, with no check against the caller's tenant). Treat
  `connector_admin` as a "can read everything" role.
- **Tenant isolation** has three layers: the vector pre filter, tenant namespaced tuples, and
  a post filter. It holds for authenticated users. It fails when identity is taken from the
  body (CM-B1) and for `/api/grab` writes (CM-H4).
- **`PANSOPH_AUTHZ_ADAPTER=none`** makes `bulk_check` allow everything. This is documented, but
  nothing warns at startup.

### ACL and identity mapping

- Grants to groups, users and "public" are all keyed by **tenant**, not by connector instance.
  The mapper docstring says users are instance scoped (`mappers.py:39-40`), but the code scopes
  them by tenant (`mappers.py:61`).
- The user subject is the email, so direct grants resolve only when a connector emits emails.
  Drive does. Slack member ids, GitHub logins and Jira accountIds never match anyone, so those
  grants under share.
- Connectors that cannot enumerate readers grant a namespaced group, such as
  `slack:member:{instance}` or `__jira_licensed_users`. Nobody holds that group until an
  operator adds a group alias. **This fails closed**, which is correct.
- IdP groups and connector groups are the same strings in the same namespace (CM-M1), so an
  IdP group that merely has the matching name bypasses the alias step.

---

## Strengths

- **The enforcement point is in the right place.** Authorization happens after candidate
  generation and before rerank, LLM and response. The reranker and the LLM never see
  unauthorized text, which the tests added here prove for REST, SSE citations, the LLM prompt
  and MCP.
- **A real ReBAC engine (SpiceDB) with consistency tokens** stored per chunk and threaded into
  the check. This protects against a stale read after a revocation.
- **ACL diff on re sync applies revocations first**, stored in a durable per document log.
- **Fail closed connector conventions.** An unresolvable grant is denied rather than shared
  wider, and Jira is careful about security levels and "anyone" grants.
- **No retrieval result cache**, so a cached answer cannot serve stale permissions.
- **The MCP tool reuses exactly the REST retrieval function** and derives identity only from a
  validated token, with no body fall back.
- **Sealed connector secrets.** Plaintext credentials are not persisted in the config column,
  and OAuth uses PKCE plus single use state kept in Postgres.
- **A small, typed, port based codebase** (about 25k lines) with a clean entry point registry.
  DOOR3 can add adapters and wrap services without touching the core.
- **Hygiene is good.** 370 hermetic tests pass, ruff is clean, CI has a permission gate, and
  releases carry an SBOM, a Grype scan and a cosign signature.

---

## Critical risks (must be closed before real DOOR3 data)

1. **CM-B1:** anyone can impersonate any user by putting an identity in the request body.
2. **CM-B2:** the SpiceDB, Postgres, Valkey, S3, OpenBao and Keycloak admin endpoints are all
   published on `0.0.0.0` with default or no credentials. Holding the SpiceDB key means holding
   authz write.
3. **CM-H1, H2, H3, H5, H8:** revocation and deletion leave stale grants or stale text behind.
4. **CM-H6:** the MCP authentication design (token as a tool argument, no audit trail) invites
   the shared service account anti pattern.
5. **CM-H7:** untrusted connector and parser code (Docling on arbitrary binaries, MCP bridge
   URLs) runs inside the agora API process with open egress. There is no sandbox, although the
   docs claim one.
6. **CM-H9:** Google Drive "anyone with the link" files become readable by the whole tenant.
7. **Stored prompt injection.** Chunk text goes verbatim into the LLM prompt and into MCP
   output. The only defense is a sentence in the prompt. Company Memory agents must treat all
   retrieved text as untrusted data.

---

## Gaps

Remediation column: **UP** = upstream contribution, **CFG** = configuration, **EXT** = Company
Memory extension (a separate service or adapter), **FORK** = fork specific patch.

### BLOCKER

#### CM-B1: body supplied identity on search and chat
- **Description:** when the token is missing, invalid, or the IdP is unreachable, `/api/search`
  and `/api/chat` use the `user`, `groups` and `tenant_id` from the JSON body. The default
  builtin identity adapter never authenticates anyone, so every request takes this path.
- **Evidence:**
  - `routes.py:63-70` (Query), `routes.py:73-80` (exceptions swallowed), `routes.py:92-113`.
  - `identity/adapters/builtin.py:35-38`.
  - `apps/lens/src/api.ts:55-62` sends the demo identity on every request.
  - The fork adds `test_cm_b1_default_config_does_not_trust_body_identity` (xfail), which
    reproduces impersonation of alice. Upstream's own test pins this behavior:
    `packages/pansoph-core/tests/test_effective_identity_units.py:85`.
- **Affected:** agora REST, the lens UI. MCP is not affected.
- **Risk:** full read of any user's and any tenant's documents without credentials. It also
  lets an attacker write arbitrary group membership tuples into SpiceDB (`routes.py:184`).
- **Remediation:**
  - Done in this fork: `PANSOPH_REQUIRE_AUTH=true` (opt in, fail closed, tested). Company
    Memory must run with it set, and with `PANSOPH_IDENTITY_ADAPTER=oidc`.
  - Upstream: make fail closed the default, keep a demo only opt in, and stop lens from sending
    body identity when a session exists.
  - **UP + CFG** (done as a small **FORK** patch pending upstream).

#### CM-B2: data plane services exposed with default credentials
- **Description:** every service in compose publishes on `0.0.0.0`:
  - SpiceDB 50051, with key `pansoph_local_dev`;
  - Postgres 5432, with password `pansoph_local_dev`;
  - Valkey 6379, with no auth;
  - SeaweedFS 3900 and 8333, with no S3 auth;
  - OpenBao 8200, in dev mode with a known root token;
  - Keycloak 8090, with admin/admin, `start-dev`, `sslRequired none`, and demo users whose
    password is their name.

  Nothing refuses to start with a default secret. Helm has no NetworkPolicy.
- **Evidence:**
  - `compose.yaml:27,34,48,66-67,87,99,103,137-138,148,284-287`.
  - `deploy/keycloak/realm-pansoph.json:4,42-86`.
  - `deploy/helm/pansoph/values.yaml:107-110`.
- **Affected:** deployment.
- **Risk:** anyone on the network can write authz tuples (granting themselves anything), read
  the whole database including sealed secrets, or mint tokens as any demo user.
- **Remediation:**
  - Build a DOOR3 compose overlay that binds everything except lens and agora to `127.0.0.1`
    or an internal network, generates every secret, and removes the demo realm.
  - Upstream: add a startup refusal of `change-me` and `*_local_dev` values, loopback binds by
    default, and a NetworkPolicy template.
  - **CFG + UP.**

### HIGH

#### CM-H1: group membership revocation never reaches the authz engine
- **Description:** each query TOUCHes `member` tuples for the caller's current effective groups
  (`routes.py:184`). Nothing ever deletes them. After a user leaves an IdP group, or an alias is
  removed, SpiceDB still says they are a member. Retrieval is correct today only because the
  vector pre filter drops documents whose `allow_groups` do not overlap the current groups, so
  the pre filter is acting as the source of truth. That contradicts invariant 1. Any other
  consumer of SpiceDB, or any doc whose pre filter passes through another group, gets the
  stale answer.
- **Evidence:** `routes.py:184-186`, `pansoph_authz/mappers.py:112`,
  `test_cm_h1_authz_engine_forgets_a_removed_group_membership` (xfail).
- **Risk:** stale access after offboarding or a team change. This is the core People Strategy
  concern.
- **Remediation:** reconcile the caller's memberships on each login or query. Read the user's
  current member tuples and delete any that are absent from the effective set, in the same
  write. Alternatively, sync memberships from the IdP (a SCIM or directory job) instead of
  from the query path. OpenFGA's contextual tuples, or SpiceDB caveats with request context,
  are the per check alternatives if persistence is not wanted. **UP.**

#### CM-H2: revocation skipped when a document yields no chunks
- **Description:** `ingest_one` returns before `_apply_acl` when `chunk_sections` is empty
  (`ingestion/pipeline.py:110-111`). A document that is emptied, or turns into an unparseable
  binary, keeps its old tuples and old chunks. Any revocation that arrived with that version is
  dropped.
- **Evidence:** `test_cm_h2_revocation_applies_even_when_new_content_is_empty` (xfail).
- **Remediation:** always reconcile the ACL. When there are no chunks, purge the document's
  chunks (and treat the document as deleted). **UP.**

#### CM-H3: stale trailing chunks after a document shrinks
- **Description:** chunks are upserted by `chunk_id = doc_id#i`. Old chunks with a higher index
  are never deleted, so text removed at the source (for example a pasted credential) stays
  retrievable by anyone who can still read the document.
- **Evidence:** `ingestion/pipeline.py:123-138`, `adapters_vector.py:60-101`,
  `test_cm_h3_removed_text_is_not_retrievable_after_resync` (xfail).
- **Remediation:** call `delete_by_doc_id` before the upsert, or delete ids at or above the new
  chunk count. **UP.**

#### CM-H4: `/api/grab` trusts tenant, instance and ACL from the body
- **Description:** any authenticated user can index content into any tenant, into any
  `instance_id`, and mark it `public` or grant it to any group. They can overwrite another
  user's grab of the same URL, and plant stored prompt injection that other users' agents will
  retrieve.
- **Evidence:** `routes.py:273-319`, `test_cm_h4_grab_cannot_write_into_another_tenant`
  (xfail).
- **Remediation:** bind the tenant to the token, restrict `allow_groups` to the caller's own
  groups, make `public` admin only, and namespace the instance per user. Company Memory should
  disable grab until then (for example by not exposing the route at the gateway). **UP + CFG.**

#### CM-H5: deleting a connector instance purges nothing
- **Description:** `delete_instance` only deletes the `connector_instances` and `sync_runs`
  rows (`connectors.py:194-200`). All chunks, tuples, ACL log rows and sealed secrets stay
  retrievable, and the credential stays stored.
- **Remediation:** purge by the doc id prefix `{connector}:{instance}:` across the vector
  store, the tuples (from `pansoph_doc_acls`) and the secrets. **UP.**

#### CM-H6: MCP authentication design
- **Description:** `pansoph_search(query, token)` takes the user's bearer token as a **tool
  argument** (`mcp_server.py:26`):
  - the token passes through the model context, transcripts and client logs;
  - the streamable HTTP transport itself is unauthenticated;
  - there is no audit call, unlike REST;
  - output is raw chunk text with no untrusted content framing.

  In practice this leads operators to paste one long lived token into agent config, which is
  the "shared MCP service account" model we must avoid.
- **Remediation:**
  - Authenticate at the transport, using the MCP authorization spec (OAuth 2.1 bearer on the
    HTTP request, validated through the Identity port).
  - Derive identity per session.
  - Audit each call.
  - Wrap results as data with source ids.
  - Company Memory should put its own MCP gateway in front (EXT) until upstream adopts this.
  - **EXT + UP.**

#### CM-H7: untrusted code and parsers run in the API process with open egress
- **Description:** the lifespan scheduler runs connector syncs inside agora (`app.py:23-45`).
  - Docling parses arbitrary binaries in process, with no size, page or time limit.
  - Drive and GitHub download whole files into memory with no cap.
  - The MCP bridge and every connector with a configurable `base_url` let a `connector_admin`
    point agora at any URL (SSRF). That includes internal services, and the stored credential
    is sent there.
  - A sealed secret handle can be referenced from any config key (`secrets.py:121-134`), so a
    connector admin can route another instance's credential to their own URL.
  - The squid egress proxy covers only scribe in `compose.hardened.yaml`. There is no
    sandbox (gVisor or Firecracker), contrary to CLAUDE.md s8.
- **Remediation:**
  - Run syncs only in scribe, with the lifespan scheduler disabled in agora.
  - Enforce egress at the network layer.
  - Add size limits and a type allowlist.
  - Resolve handles only for declared secret keys.
  - Add an allowlist of host patterns per connector.
  - **CFG + UP.**

#### CM-H8: GitHub permission changes do not propagate after the first sync
- **Description:** incremental GitHub syncs only yield files touched by new commits
  (`connectors/github/.../connector.py:281-292`), and nothing calls `list_acls`. A repo made
  private, or a collaborator or team that is removed, keeps its old grant on every unchanged
  file.
- **Remediation:** re list permissions for each repo on every run and re apply them to all of
  its documents (the ACL is the same for the whole repo), or call `list_acls` on a schedule.
  **UP.**

#### CM-H9: Google Drive "anyone with the link" becomes tenant public
- **Description:** every `type=anyone` permission maps to `public=True`
  (`connectors/google_drive/.../connector.py:487-488`), including
  `allowFileDiscovery=false`. Link only files become searchable to everyone in the tenant.
  Files moved out of the configured folder are not tombstoned (`:319-320`).
- **Remediation:** treat link only sharing as no grant, or as a distinct opt in group. Tombstone
  files that leave the folder. **UP.**

#### CM-H10: vulnerable dependencies and unlocked builds
- **Description:** `pip-audit` (run 2026-09-29) found 16 advisories in 5 packages:
  - starlette 1.2.1 (fix 1.3.1);
  - cryptography 48.0.0 (fix 48.0.1 to 50.0.0);
  - mcp 1.27.2 (fix 1.28.1);
  - anyio 4.13.0 (fix 4.14.2);
  - pydantic-settings 2.14.1 (fix 2.14.2).

  lens has react-router advisories (2 high, 3 moderate). Builds weaken the lockfile:
  - `Dockerfile.agora:12-13` and `Dockerfile.scribe` run `uv sync --frozen || uv sync`;
  - `Dockerfile.lens` copies a `package-lock.json` that does not exist, so it always falls back
    to an unlocked `npm install`.
- **Remediation:** make targeted bumps of those five packages plus react-router, in their own
  change, and remove the `||` fall backs. Not done here, to respect the "no broad upgrades"
  constraint. **UP.**

### MEDIUM

- **CM-M1: shared namespace for identity groups and connector groups.** An IdP group string
  equal to a connector group (`slack:member:x`, a GitHub team slug, a Jira group name, a Drive
  group email) grants access without an alias. GitHub team slugs and Jira groups from two orgs
  or sites in one tenant merge. `__jira_licensed_users` is global per tenant. **Evidence:**
  `mappers.py:47-61`, `test_cm_m1_...` (xfail). **Remediation:** prefix identity groups (for
  example `idp:`) and prefix connector groups with the instance. **UP.**
- **CM-M2: source principals other than email never resolve.** Slack member ids, GitHub logins
  and Jira accountIds in `allow_users` match nobody, so private Slack channels and GitHub
  collaborators under share. This fails safe, but blocks those sources' value. **Remediation:**
  an identity linking table (a source principal mapped to a user email), resolved at query
  time like aliases. **EXT** (or UP).
- **CM-M3: OIDC hardening.**
  - `email_verified` is not checked.
  - `exp` is not required.
  - There is no `azp` or `typ` check.
  - The fall back subject is `preferred_username`, which users can change.
  - The user cache grows without limit.
  - An IdP outage becomes anonymous access (see CM-B1).

  **Remediation:** require verified email or map by `sub` through the linking table, require
  `exp`, and fail closed on errors. **UP.**
- **CM-M4: admin surfaces are not tenant bound.** Instance CRUD, sync, the documents snippet
  read, and alias and OAuth routes take the tenant from the body, query or instance row. One
  connector admin can manage every tenant. `create_instance` upsert can re home another
  instance to a new tenant. DOOR3 runs a single tenant, so the risk there is low, but the
  documents route still gives any connector admin snippets of all content. **UP.**
- **CM-M5: unauthenticated inventory disclosure.** `GET /api/connectors/instances` and `/runs`
  expose configured sources, channel and repo and folder ids, scope emails, and error strings.
  **UP** (require auth).
- **CM-M6: one bad document aborts a sync.** An exception ends the run, so the ACL updates and
  revocations of every later document in that run are skipped until the next successful run
  (`connectors.py:408-410`). **UP.**
- **CM-M7: Slack connector issues.**
  - `channel_ids` is declared but never read, so every reachable channel is ingested.
  - `auto_join` defaults to true, and `channels:join` is requested.
  - Only the first history page (about 100 messages) is read, with no threads.
  - There are no deletions for archived channels, or for channels the bot has left.

  **UP.**
- **CM-M8: audit trail is not persisted.** No handler is configured for `pansoph.audit`, so
  INFO records are dropped under Python defaults. MCP is not audited, and query text is not
  recorded. **CFG** (logging config) plus **UP** (MCP audit).
- **CM-M9: RLS is claimed but not applied.** `deploy/security/rls.sql` is never run, and agora
  connects as the Postgres superuser, which bypasses FORCE RLS anyway. **CFG/UP**, or correct
  the docs.
- **CM-M10: lens security headers and Keycloak client.**
  - nginx sets no CSP or other security headers.
  - The Keycloak lens client has password grant on and `webOrigins: *`.

  **CFG.**
- **CM-M11: CI supply chain.**
  - Actions are pinned by tag, not SHA.
  - `ci.yml` has no `permissions:` block.
  - Base and service images use tags (`latest` for several).

  **UP.**

### LOW

- **CM-L1: chat with zero permitted hits answers from general knowledge.** For Company Memory
  this should be a refusal, so an agent does not present a guess as company fact
  (`routes.py:238`). **EXT** (the Company Memory layer should not call `/api/chat`; it should
  call search and compose its own answer).
- **CM-L2: pre filter under shares.** A document granted to a group and also to a user directly
  (or public plus a group) is dropped by the group overlap pre filter for a direct grantee who
  is not in the group. This is a recall bug, not a leak. **UP.**
- **CM-L3: Qdrant is dense only,** so there is no hybrid retrieval. Use pgvector for the POC.
- **CM-L4: minor exposure.** `/docs` and `/api/meta` are exposed, the otel collector uses the
  `debug` exporter, and `seed-demo.sh` passes the password on the command line. **CFG.**
- **CM-L5: the envelope root key is read as raw UTF-8,** while the Helm comment says base64.
  **UP** (docs).

---

## Connector readiness

All tests for these connectors run against recorded fixtures or mocked HTTP. **None has a live
run in this repository's evidence except GitHub**, which the upstream README says was run
against a public repo. No connector below is ready, because CM-B1 and CM-H1 affect all of them.

| Source | Verdict | Why |
|---|---|---|
| **Slack** | **PARTIAL** | Scopes are read focused, but `channels:join` and `auto_join=true` widen the bot's reach and post join notices. Public channels map to `slack:member:{instance}`, which fails closed until aliased. Private channels map to member ids, which never resolve (CM-M2). `channel_ids` is ignored. There is no purge of archived or left channels, and only about 100 messages per channel with no threads. DMs are excluded, which is correct. |
| **GitHub** | **NOT READY** | Revocation and visibility changes do not propagate after the first sync (CM-H8). Team slugs are unprefixed and collide (CM-M1). Collaborator logins never resolve (CM-M2). The `repo` scope is read and write, so prefer a GitHub App or a fine grained PAT. Renames leave old paths, `max_files` truncation advances the cursor, and base64 content has no size cap. |
| **Jira** | **PARTIAL** | The most faithful ACL model: it maps the BROWSE_PROJECTS scheme and security levels, and fails closed. It re lists every issue on each run and detects deletions. However, with OAuth the permission reads are unscoped, so every issue fails closed to nobody (it only works with a Basic auth admin credential). `__jira_licensed_users` is shared across sites, group names collide, and accountIds never resolve. Only the summary and description are ingested. |
| **Google Drive** | **NOT READY** | Emails match the subject (the best identity fit), the scope is `drive.readonly`, and the changes feed catches deletions and loss of access. However, "anyone with link" becomes tenant public (CM-H9), files moved out of the folder keep stale ACLs, downloads have no size cap, and `raise_for_status` is skipped on some metadata calls. |
| Meeting transcripts | **NOT IMPLEMENTED** as a DOOR3 source | A `granola` connector exists via the MCP bridge, with the ACL set by the operator (not per meeting attendees). |

---

## MCP readiness

**Not safe to expose to internal agents yet.**

- **Positive:**
  - The tool derives identity only from a validated token.
  - It runs exactly the REST enforcement path. Parity was tested here for three users across
    two tenants.
  - It returns "Authentication required" on a missing or invalid token.
  - It has no body identity fall back.
- **Blocking:**
  - The token is a tool argument, so it is visible to the model and its logs.
  - The HTTP transport is unauthenticated.
  - There are no audit events.
  - Output is raw text, so stored prompt injection reaches the agent verbatim.
  - The service is not deployed in compose or Helm.
  - The shared service token model is the easy path.

**Required model for Company Memory:** a user signs in to the agent client, which obtains a
user scoped OAuth token (audience = the Company Memory MCP). The token is carried in the MCP
transport `Authorization` header, and the gateway validates it and derives the Pansoph identity
per session. Tokens stay out of tool arguments and prompts. Agents never get vector DB or
SpiceDB credentials. Service agents (IT Ops batch jobs) act only on behalf of a named user, or
with an explicit narrowly scoped service principal that has its own ACL grants. They never hold
an "all access" identity.

---

## Production readiness

| Stage | Status | Conditions |
|---|---|---|
| **Local POC with synthetic or public data** | **Ready now** | Run locally, bind to loopback, set `PANSOPH_REQUIRE_AUTH=true` and the OIDC adapter, and use no real DOOR3 content. |
| **Internal pilot with real DOOR3 data (a small, consenting group)** | **Not ready** | Close CM-B1 (config plus lens), CM-B2 (a hardened overlay), CM-H1, CM-H2, CM-H3, CM-H5, and the MCP gateway (CM-H6). Keep grab disabled (CM-H4). Run syncs in scribe only with network egress control (CM-H7). Use only connectors whose gaps are closed or accepted (for example Drive after CM-H9, and Jira with Basic auth). Persist audit logs. Bump vulnerable dependencies. |
| **Production** | **Not ready** | All of the above plus: identity linking (CM-M2), namespace separation (CM-M1), tenant bound admin (CM-M4), a sandbox for connector and parser work, TLS everywhere, RLS or a documented equivalent, migrations, SHA pinned CI, a live permission test per connector against real sources, and a revocation SLA measured end to end. |

---

## Company Memory fit

Pansoph should be the **retrieval and authorization substrate**. Company Memory services call
`search` as the user and do their own composition, reporting and structured memory extraction
over the results they receive (which are already authorized).

### IT Ops agent

- **Infrastructure questions, prior incidents, recurring issues:** a good fit. Jira, Slack
  incident channels, GitHub and runbooks in Drive map naturally to hybrid search. The chunk
  level citations (`doc_id`, `chunk_id`) give traceability.
- **Infrastructure ownership:** partial. Ownership often lives in metadata (CODEOWNERS, Jira
  components, assignees) that the connectors do not ingest as structured fields. A Company
  Memory extraction service would derive it from authorized retrieval results.
- **Operational reports:** these must be generated per requesting user from their authorized
  results. An aggregate report that is shared wider must not be built from content some
  readers cannot see (the aggregation leak). Report generation belongs outside Pansoph, and
  must record the identity it ran as and cite its sources.

### People Strategy Ops agent

- **Project experience, skills evidence, participation:** feasible as **evidence retrieval**.
  Search returns authorized documents in which a person appears (commits, Jira issues, Slack
  threads, docs), with citations.
- **Hard constraints for this use case:**
  - Retrieval runs as the requesting HR or staffing user, never as a super identity. HR access
    to wider content must be an explicit, audited, policy approved grant.
  - Outputs are evidence and context with citations, **never scores, rankings or automated
    staffing or personnel decisions**. Structured People, Skills or Experience memories are
    derived artifacts. They must keep their provenance and the ACL of their sources, and must
    not be served to users who cannot read those sources.
  - Revocation (CM-H1, CM-H2, CM-H3, CM-H5) and identity linking (CM-M2) matter most here,
    because leavers, private channels and restricted HR documents are the most sensitive cases.
  - Slack private channels and GitHub collaborators are unusable until CM-M2 is done, which
    limits skills evidence.
- **Aggregate capability reports:** acceptable only as counts or summaries over authorized
  evidence, with the report's audience restricted to people who could see the inputs, or with
  aggregation thresholds agreed with People Strategy and Legal.

---

## Recommendation

**Suitable with conditions.** The enforcement architecture is sound and the gaps are local.
Conditions:

1. `PANSOPH_REQUIRE_AUTH=true` plus the OIDC adapter against DOOR3's IdP, and lens no longer
   sends a body identity (CM-B1).
2. A hardened deployment overlay: loopback or internal only data services, generated secrets,
   no demo realm, and a startup refusal of defaults (CM-B2).
3. Fix the revocation and deletion gaps CM-H1, H2, H3 and H5, each with a permission test
   (the xfail tests above flip to passing).
4. Put a Company Memory MCP gateway with transport level user auth and auditing in front of
   Pansoph. Retire the token argument (CM-H6).
5. Syncs and parsing only in scribe with network egress control and size limits. Grab
   disabled (CM-H4, H7).
6. Connector fixes before each source is enabled: Drive (CM-H9), GitHub (CM-H8), Slack
   (CM-M7), Jira (Basic auth or scoped OAuth, CM-M1).
7. Targeted dependency bumps (CM-H10) and persisted audit logs (CM-M8).
8. Identity linking (CM-M2) and namespace separation (CM-M1) before the People Strategy use
   cases.
