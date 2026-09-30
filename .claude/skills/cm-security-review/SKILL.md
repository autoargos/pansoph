---
name: cm-security-review
description: Company Memory security review of a change, PR, or component in this pansoph fork. Traces identity, authorization, retrieval, MCP and API exposure, tenant isolation, revocation, and connector security against the fork's invariants. Use for any change touching retrieval, authz, identity, routes, MCP, ingestion ACLs, connectors, or deployment. Complements the generic built-in /security-review.
---

# Company Memory security review

Review against the invariants in `CLAUDE.md` Part 1 section 5 and the gap list in
`docs/company-memory/SECURITY_AND_FIT_REVIEW.md`. Evidence comes from code and tests, never from
docs alone.

## 1. Scope the change

- `git diff` (or the named PR, branch, or path). List every touched file under:
  `packages/pansoph-retrieval`, `packages/pansoph-authz`, `packages/pansoph-identity`,
  `packages/pansoph-ingestion`, `packages/pansoph-core/src/pansoph_core/{routes*,mcp_server,connectors,secrets,oauth}`,
  `connectors/*`, `deploy/`, `compose*.yaml`, `.github/workflows`.
- If none of these are touched, say so and stop at a short note.

## 2. Trace every path that can emit content

For each new or changed path that returns text, titles, ids, snippets, counts, scores, errors,
or logs, to a user, an LLM, an agent, or an MCP client, answer:

1. **Identity.** Where does the subject, groups, or tenant come from? It must be a validated
   token via the Identity port. Flag body fields, tool arguments, headers, defaults, and
   `except Exception` blocks that turn a failure into anonymous or a fall back identity.
2. **Authorization.** Does it go through `pansoph_retrieval.pipeline.retrieve` (a bulk check
   after candidate generation, before rerank, LLM, or response)? Any direct vector store,
   `pansoph_chunks`, or SpiceDB read that returns content is a finding unless it is a gated,
   audited admin route. Say which role gates it.
3. **Fail closed.** What happens on an authz or IdP error or timeout? It must deny.
4. **Tenant.** Is every query predicated on the token's tenant? Look for IDOR by `instance_id`
   or `doc_id`.
5. **Leak channels.** Check the response fields, SSE `source` events, the LLM prompt, the
   reranker input, error messages, `sync_runs.error`, audit fields, and counts.
6. **Revocation.** For ingestion or connector changes: do removed grants, members, documents,
   instances, and text leave both SpiceDB and the vector store? Check the zero chunk and
   shrink paths (CM-H2, CM-H3).
7. **MCP.** Is identity taken from the transport, not a tool argument? Is the call audited? Is
   the output framed as untrusted data? Is there any shared service identity?
8. **Connectors.** Scopes, base URL and SSRF, secret handle use, size limits, and ACL mapping
   faithfulness (run `connector-review` for depth).
9. **Deploy.** Binds, default secrets, TLS, exposed admin UIs, pinned images and actions.

## 3. Verify with tests

- Run `uv run pytest -q -m permission`.
- Where a finding is testable, write the test (see `permission-test`). A gap that cannot be
  fixed in this change goes in `tests/permission/test_company_memory_known_gaps.py` as
  `xfail(strict=True)` with a new `CM-*` id.

## 4. Report

Findings grouped by severity: BLOCKER, HIGH, MEDIUM, LOW. Each finding gives `file:line`, the
concrete failure scenario, and the remediation class: upstream contribution, config, Company
Memory extension, or fork patch. If a finding is new, add it to the review document's gap list.
Do not claim something is safe without naming the test or code that shows it.
