---
name: connector-review
description: Evaluate a pansoph source connector (Slack, GitHub, Jira, Google Drive, transcripts, or a new one) for Company Memory readiness, covering auth and scopes, ACL and identity mapping fidelity, incremental sync, revocation and deletion, SSRF, size limits, and tests. Produces a READY / PARTIAL / NOT READY / NOT IMPLEMENTED verdict. Use before enabling a connector on real data or when changing one.
---

# Connector review

A connector that maps ACLs wrongly leaks silently. Review the code against the source's
**current official API docs** (upstream CLAUDE.md connector rule). Fetch them; do not work from
memory. Record the doc links and the date checked.

## Read

- `connectors/<name>/src/.../connector.py`, its README, its tests and fixtures.
- `packages/pansoph-connector-sdk/src/pansoph_connector_sdk/{contract,auth*,conformance}.py`.
- `packages/pansoph-authz/src/pansoph_authz/mappers.py` (the tenant scoped namespace).
- `packages/pansoph-core/src/pansoph_core/oauth/providers/<provider>.py` (the scopes requested).

## Evaluate, with file:line evidence

1. **Credential and scopes.** Which credential types are supported? Are the scopes least
   privilege and read only? Flag write or join scopes, and actions that widen reach (for
   example auto join).
2. **Credential reach versus user reach.** Map ACLs to what the source says each user can see,
   not to what the credential can see.
3. **ACL mapping.** Public, org, domain, group, team, role, and user. Is "anyone with link" or
   "all org" mapped narrowly? Is every unresolvable grant denied (fail closed)?
4. **Identity mapping.** Do user principals match the pansoph subject (email)? Source ids
   (Slack member ids, GitHub logins, Jira accountIds) resolve to nobody today (CM-M2).
   Group names are unprefixed and collide across instances and with IdP groups (CM-M1).
5. **Incremental sync.** Does a permission change **without** a content change get picked up?
   Are membership changes picked up? Does a truncated page advance the cursor? Do renames,
   moves, and out of scope items tombstone?
6. **Deletions.** Deleted, archived, or trashed items, and loss of credential access, must each
   produce a tombstone.
7. **Safety.** Is the `base_url` configurable (SSRF, credential forwarding)? Are downloads size
   capped, is there a type allowlist, and are values safely interpolated into queries or URLs?
   What does it do on an HTTP error (fail closed, or abort the sync)?
8. **Tests.** Fixture or mock only, or a live run? Does it pass `assert_conforms`? Is there a
   permission test for its ACL mapping (`tests/permission/test_connector_acl_regression.py`)?

## Verdict

- **READY**: faithful ACLs, revocation and deletion propagate, identities resolve, and a live
  run exists.
- **PARTIAL**: fails closed but under shares, or needs aliases or config, with no leak path.
- **NOT READY**: any over share or stale grant path.
- **NOT IMPLEMENTED**.

Update the "Connector readiness" table in `docs/company-memory/SECURITY_AND_FIT_REVIEW.md`, and
add a strict xfail test for each leak path you find (`permission-test`).
