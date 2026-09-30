# Upstream strategy

The goal: DOOR3 can pull every upstream Pansoph release with a routine merge, and the fork's
divergence stays small, visible, and justified.

## Remotes

| Remote | URL | Role |
|---|---|---|
| `origin` | `git@github.com:Door3Dev/company-memory-pansoph.git` | the DOOR3 fork |
| `upstream` | `https://github.com/autoargos/pansoph.git` | the open source project |

Set it up once per clone:

```bash
git remote add upstream https://github.com/autoargos/pansoph.git
git fetch upstream
```

## Fork strategy

- `main` tracks upstream `main` plus a small, documented set of fork commits.
- Sync with a **merge** of `upstream/main` into a branch off `main` (not a rebase of published
  history), then a PR. Review the incoming diff with the `upstream-sync-review` skill.
- Every fork commit is small and atomic. Prefix its subject with the conventional type and
  mention `fork:` in the body when it touches an upstream file, so
  `git log upstream/main..main` reads as a patch list.
- Anything fixable upstream is proposed upstream. Once upstream merges it, the fork's copy is
  dropped at the next sync.

## Where DOOR3 specific things live

| Kind | Location | Upstream files touched |
|---|---|---|
| Fork docs | `docs/company-memory/` | none |
| Fork tests | `tests/permission/test_company_memory_*.py`, `tests/_agora_harness.py` | none |
| AI agent skills | `.claude/skills/` | none (upstream has none) |
| Company Memory services (gateway, memory extraction, reporting, agents) | a **separate repository or service**, calling agora over REST and MCP | none |
| DOOR3 adapters (for example an identity linker, or a DOOR3 IdP mapping) | a new package under `packages/` or `connectors/` named `door3-*` or `company-memory-*`, registered through entry points | none |
| Deployment for DOOR3 | a new overlay (for example `deploy/company-memory/compose.yaml`) layered on the upstream compose | none |
| Unavoidable core patches | in place, minimal, listed below | yes |

## Preferred extension patterns (in order)

1. **Configuration.** Environment variables, compose overlays, Helm values.
2. **A new adapter behind an existing port**, registered via entry points (`pansoph.identity`,
   `pansoph.authz`, `pansoph.connectors`, and so on). No core edit.
3. **A separate service** that calls agora as the user (the Company Memory layer, an MCP
   gateway).
4. **An upstream contribution** (a PR to `autoargos/pansoph`) for a bug or a missing security
   control.
5. **A fork patch**, only when 1 to 4 cannot work or are blocked for now. Keep it small, keep
   it in its own commit, and record it below.

## Handling upstream modifications

- Do not reformat, rename, or reorganize upstream files. Do not change `CLAUDE.md`'s upstream
  technical sections except to annotate them.
- When a fork patch conflicts at merge time, prefer upstream's version, then re apply the patch
  if it is still needed.
- When upstream fixes a gap listed in the review, remove the matching `xfail` marker in
  `tests/permission/test_company_memory_known_gaps.py` (strict xfail makes the test fail until
  you do) and update the review.

## Identifying an unavoidable fork patch

A change to an upstream file is justified only if all of these hold:

- it closes a security gap or enables a Company Memory requirement;
- configuration, an adapter, or an external service cannot achieve it;
- it is additive or opt in where possible, so upstream behavior is unchanged by default;
- it has a test and an entry in the table below.

## Fork patch register

| Date | Files | Change | Gap | Upstream status |
|---|---|---|---|---|
| 2026-09-29 | `packages/pansoph-core/src/pansoph_core/routes.py`, `.env.example` | Opt in `PANSOPH_REQUIRE_AUTH`: a missing or invalid token is a 401 on search and chat instead of falling back to the body identity. Default unchanged. | CM-B1 | to propose |
| 2026-09-29 | `CLAUDE.md` | Fork preamble plus Company Memory sections. Upstream guide kept as a section. | n/a | fork only |
| 2026-09-29 | `pansoph_core/memberships.py` (new), `routes.py` (`resolve_subject`), `assembly.py` | Reconcile subject memberships: delete stale member tuples before the check; check at the membership write's freshness. | CM-H1 | to propose |
| 2026-09-29 | `pansoph_retrieval/pipeline.py` | `retrieve(at_least_as_fresh=...)` optional override of the candidate derived token. Default behavior unchanged. | CM-H1 | to propose |
| 2026-09-29 | `pansoph_ingestion/pipeline.py` | Purge a document that yields no chunks; delete old chunks before re-upsert; `purge_documents` helper. | CM-H2, CM-H3, CM-H5 | to propose |
| 2026-09-29 | `pansoph_ingestion/acl_log.py`, `adapters_acl_log.py` | `doc_ids_with_prefix` (not on the port). | CM-H5 | to propose |
| 2026-09-29 | `pansoph_core/connectors.py`, `routes.py` (delete route) | Deleting an instance purges its documents, tuples, ACL records, and sealed secrets. | CM-H5 | to propose |
| 2026-09-29 | `pansoph_identity/adapters/oidc.py`, `assembly.py` | Opt in groups from userinfo (fail closed); require `exp`, `iss`, `aud`, `sub`; ignore unverified email. | CM-H1, CM-M3 | to propose |
| 2026-09-29 | `pansoph_identity/adapters/oidc.py`, `assembly.py` | Opt in `require_verified_email` (`OIDC_REQUIRE_VERIFIED_EMAIL`): deny a token without an email the IdP marks verified. Default unchanged. | CM-M3 | to propose |
| 2026-09-29 | `pansoph_core/mcp_auth.py` (new), `mcp_server.py` | MCP bearer auth at the transport; `pansoph_search(query)` no longer takes a token; MCP audit. **Breaking** for `pansoph-mcp` callers that passed `token` (they now send the header). | CM-H6 | to propose |
| 2026-09-29 | `pansoph_retrieval/adapters_vector.py` | `PgVectorScaleStore.get_document_chunks` (not on the port; caller must authorize first). | n/a (source read) | to propose |
| 2026-09-29 | `tests/_fakes.py`, `tests/test_ingest_retrieve.py` | Fakes gain `delete_by_doc_id` (a port method the local fake lacked), `get_document_chunks`, and an authz only mode. | n/a | test only |

Fork owned additions (no upstream file touched): `packages/company-memory-mcp/`,
`connectors/company_memory_synthetic/`, `deploy/company-memory/`,
`docs/company-memory/DEMO.md`, and `tests/_company_memory_demo.py` plus
`tests/permission/test_company_memory_mcp.py` and
`tests/security/test_company_memory_hardening.py`. `uv.lock` gained the two new workspace
members; no dependency versions changed.

The manual knowledge capability (MCP write tools, the manual source, the publish policy)
is fork owned too: `company_memory_mcp/knowledge.py`, `knowledge_store.py`,
`publish_policy.py`, `deploy/company-memory/publish-policy.json`,
`deploy/company-memory/demo-input/`, and
`tests/permission/test_company_memory_knowledge.py`. It touches no upstream file: it
drives the existing `IngestionPipeline` and `retrieve()` unchanged.

The real-user local profile is fork owned as well: the `company_memory` identity adapter
(`company_memory_mcp/identity.py`, registered on the `pansoph.identity` entry point, the
preferred "new adapter behind an existing port" pattern), `real_users.py`, `cli_real.py`,
`deploy/company-memory/users.example.json`, and
`tests/permission/test_company_memory_real_users.py`. Its only upstream file change is the
`require_verified_email` opt in above.

