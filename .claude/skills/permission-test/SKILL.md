---
name: permission-test
description: Write and run authorization and leakage tests for this pansoph fork (cross user, group removal, revocation, deletion, cross tenant, MCP versus REST parity, metadata and citation leakage). Use whenever a change touches retrieval, authz, identity, routes, MCP, ingestion ACLs, or connector ACL mapping, or when asked to prove a permission property.
---

# Permission tests

Permission tests gate releases. They must assert a real security property with concrete
restricted content, never `assert a == a`.

## Where tests go

- Pipeline level (`retrieve()` with fakes): the upstream style, `tests/permission/test_redteam.py`.
- **Route level (preferred for Company Memory):** `tests/_agora_harness.py` mounts the real
  agora router and the real MCP tool on the in-memory fakes. Use `build_harness(monkeypatch)`,
  then `h.search(token, **body)`, `h.chat(...)`, `h.mcp_search(token)`, `h.sync(instance,
  tenant)`, and `h.connectors[...]` to mutate the source.
- Fork tests: `tests/permission/test_company_memory_boundaries.py` (passing invariants) and
  `tests/permission/test_company_memory_known_gaps.py` (strict xfail for open gaps).
- Live engines: `tests/integration/` (`@pytest.mark.integration`, needs
  `deploy/compose.test.yaml`).

## Checklist for a change

Pick every row that applies and make sure a test covers it:

| Property | Shape |
|---|---|
| Cross user | user A's restricted doc is absent for B: ids, text, count |
| Group removal | the doc is visible with the group, and gone on the next query without it (also check the authz engine itself, CM-H1) |
| Revocation | allowed, then ACL narrowed, then re-sync, then the same query returns nothing |
| Deletion | tombstone, then re-sync, then gone from REST and MCP |
| Cross tenant | a token for tenant B with `tenant_id` A in the body sees only B |
| MCP parity | the set of MCP ids equals the set of REST ids for every test user, and an invalid token returns nothing |
| Metadata leak | restricted markers absent from the response JSON, SSE `source` events, and every `FakeLLM.prompts` entry |
| Fail closed | with `PANSOPH_REQUIRE_AUTH=true`, a missing or invalid token is a 401 and writes no tuples |

Use unique marker strings (for example `ENG_SECRET ...`) in restricted docs and assert they are
absent from the whole response text, not just from parsed ids.

## Rules

- Mark with `pytestmark = pytest.mark.permission`.
- A gap you find but do not fix: `@pytest.mark.xfail(strict=True, reason="CM-XX: ...")`, and
  add CM-XX to `docs/company-memory/SECURITY_AND_FIT_REVIEW.md`.
- A fix: remove the xfail marker in the same change, and update the review.
- Never loosen an assertion, skip a test, or switch the authz adapter to `none` to get green.
- Synthetic data only.

## Run

```bash
uv run pytest -q -m permission
uv run pytest -q tests/permission -rxX
```

To confirm an xfail fails for the right reason: `uv run pytest <file> --runxfail`.
