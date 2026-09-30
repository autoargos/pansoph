# DOOR3 Company Memory on Pansoph

This directory is the fork's own documentation. Everything else in the repository is upstream
Pansoph (`autoargos/pansoph`) unless a file says otherwise.

## What Company Memory is

Company Memory is DOOR3's internal, secure memory and knowledge layer. Employees and internal
AI agents use it to answer questions from company knowledge, generate reports, find prior
project experience and engineering expertise, and retrieve historical technical decisions. The
key requirement:

> A user or AI agent can only retrieve information that the authenticated user is authorized
> to access, and authorization happens before anything reaches the LLM or the calling agent.

## Why Pansoph

Pansoph is an Apache 2.0, self hostable knowledge platform built around exactly that
requirement. It enforces document level access control at retrieval, with one bulk check
against a ReBAC engine (SpiceDB) after candidate generation and before rerank, LLM and
response. It has a port and adapter architecture, so DOOR3 can wrap it with services and
adapters instead of rewriting it. The evaluation of that claim against the code is in
[`SECURITY_AND_FIT_REVIEW.md`](SECURITY_AND_FIT_REVIEW.md).

## What Pansoph is responsible for (the substrate)

- Ingestion from sources through connectors, including each source's ACL.
- Parsing, chunking and indexing (hybrid dense plus sparse).
- Permission aware retrieval: the authz bulk check, deny filter and tenant isolation.
- ACL synchronization and revocation.
- Identity (OIDC) and authorization (SpiceDB).
- Citations: every hit carries `doc_id` and `chunk_id` back to its source.
- Secure API and MCP access to retrieval.

## What Pansoph is NOT responsible for

These live in separate Company Memory services that call Pansoph **as the user**:

- People, Project, Skills, Decisions, Technologies, Problems, Solutions and Experience memory
  (structured extraction and storage).
- Reporting and aggregation.
- Agents (IT Ops, People Strategy, Engineering and Delivery) and their workflows.
- Long term structured memory and any graph model.
- Agent specific prompts, tools and orchestration.

A derived memory must keep the provenance and ACL of the sources it came from. It must never be
served to someone who could not read those sources.

## Target sources

Slack, Jira, GitHub, Google Drive, meeting transcripts, and internal documents. Other company
systems may come later. Current readiness is in the review, under "Connector readiness".

## Target consumers

The IT Operations agent, the People Strategy Operations agent, Engineering and Delivery agents,
other internal agents, and humans through the lens UI or other clients.

## Security principles

1. No content is returned without an authorization decision. When authorization cannot be
   evaluated, the answer is deny.
2. Identity always comes from a validated token. It never comes from request bodies, tool
   arguments, or defaults.
3. Tenant context is mandatory and comes from the token.
4. MCP and any agent interface enforce exactly the REST rules. There is no shared superuser MCP
   identity, and agents get no raw vector DB or authz engine access.
5. Revocation and deletion propagate to the authz engine, the vector store, and every derived
   artifact.
6. Every factual answer keeps its source evidence (citations).
7. Retrieved text is untrusted data. It can contain prompt injection.
8. People related outputs are evidence and context. They are never scores, rankings, or
   automated personnel decisions.

## Current POC status (2026-09-29)

- **Local synthetic demo: runnable end to end.** See [`DEMO.md`](DEMO.md). The flow is:
  Claude Code, then the authenticated Company Memory MCP (`packages/company-memory-mcp`),
  then pansoph retrieval, then SpiceDB, over 11 fictional documents.
  `uv run company-memory verify` checks the permission matrix, REST and MCP parity, the
  doc id bypass, and revocation against the live stack.
- **Manual knowledge through MCP: runnable end to end.** A signed in user can add a
  document through Claude Code (`add_company_memory_knowledge`). It is private by default,
  shared with a group only under an explicit publish grant, and indexed by the normal
  pansoph pipeline. Other users retrieve it only as their permissions allow. See step 11
  of `DEMO.md`.
- **Real DOOR3 identities, synthetic knowledge: runnable locally.** Real users are
  configured in a gitignored `users.local.json` and live in their own tenant (`door3`).
  Every configured user reads the `it-support` scope; only `it-support-publishers` may
  publish to it. See step 12 of `DEMO.md`. Real corporate knowledge is still not ingested.
- **Real DOOR3 data:** still blocked. The remaining gaps are in the review's status update
  (CM-H7, the connector gaps CM-H8, CM-H9 and CM-M7, identity linking CM-M2, and namespace
  separation CM-M1).
- Open gaps are pinned as strict expected failure tests in
  `tests/permission/test_company_memory_known_gaps.py`.

## Fork specific configuration

| Setting | Value for Company Memory | Why |
|---|---|---|
| `PANSOPH_REQUIRE_AUTH` | `true` | Fail closed. No body identity fall back (CM-B1). Added by this fork. |
| `PANSOPH_IDENTITY_ADAPTER` | `company_memory` | OIDC plus the Company Memory rules: the principal is the verified email, and only configured users authenticate. The builtin adapter authenticates no one. Fork adapter (`company_memory_mcp.identity`). |
| `OIDC_REQUIRE_VERIFIED_EMAIL` | `true` | A token without a verified email is rejected (CM-M3). Added by this fork. |
| `CM_REQUIRED_GROUPS` | `everyone,cm-service-accounts` | The configured-user groups. An IdP account outside them gets a 401 on MCP and REST. Fork setting. |
| `PANSOPH_AUTHZ_ADAPTER` | `spicedb` | Never `none`. |
| `PANSOPH_VECTOR_ADAPTER` | `pgvectorscale` | Hybrid retrieval. Qdrant is dense only today. |
| `OIDC_GROUPS_FROM_USERINFO` + `OIDC_USERINFO_URL` | `true`, the IdP userinfo URL | Group removals apply on the next request (CM-H1). Added by this fork. |
| `PANSOPH_MCP_PUBLIC_URL` | the MCP URL | Required: MCP is a resource server with transport auth (CM-H6). |
| `CM_PUBLISH_POLICY_FILE` | `deploy/company-memory/publish-policy.json` (mounted read only) | Who may publish manual knowledge to which group scope. Unset means private knowledge only. Fork package setting. |

## Documents here

- [`SECURITY_AND_FIT_REVIEW.md`](SECURITY_AND_FIT_REVIEW.md): the security audit, the gaps, and
  the fit assessment.
- [`UPSTREAM_STRATEGY.md`](UPSTREAM_STRATEGY.md): how the fork stays mergeable with upstream.
- [`DEMO.md`](DEMO.md): the local synthetic demo runbook.
