# pansoph-connector-jira

Ingests issues from a Jira Cloud project (REST API v3) into pansoph, mapping each issue's access faithfully from the project's permission scheme and its issue security level. Access always fails closed: the connector is structurally incapable of emitting `public=True`, so a mapping it cannot resolve under-shares rather than leaks.

## What it syncs

One document per Jira issue in the configured project. The document text is the issue `summary`, followed by the `description` flattened from Atlassian Document Format (ADF) into plain text (every `text` leaf node concatenated in document order). The title is the summary. The connector reads only three fields per issue: `summary`, `description`, and `security`. The change feed reads `updated` (and the always-present `key`) to enumerate issues.

## Setup

Two auth paths are supported; configure exactly one.

**Atlassian OAuth 2.0 (3LO)** via the in-app Connect flow (`oauth_provider="atlassian"`):

1. Create an OAuth 2.0 (3LO) app in the Atlassian developer console with granular scopes enabled (see below).
2. Set its callback URL to your pansoph OAuth redirect endpoint (the pattern `<pansoph-base>/api/oauth/atlassian/callback`).
3. Provide the app credentials to pansoph as secrets `PANSOPH_OAUTH_ATLASSIAN_CLIENT_ID` and `PANSOPH_OAUTH_ATLASSIAN_CLIENT_SECRET`.
4. Sign in through the connector's "Connect with Atlassian" field. The minted Bearer token is sealed into config key `oauth_token`.

A 3LO token is not scoped to a site origin. After consent the connector resolves the cloud-id from the token via `GET https://api.atlassian.com/oauth/token/accessible-resources` and addresses the API at `https://api.atlassian.com/ex/jira/{cloud_id}`. This resolution runs once per sync and is cached. If the grant reaches several sites, disambiguate with the optional `cloud_id` (exact id match) or `site_url` (substring match) config fields; otherwise the first accessible site is used.

**Basic auth (manual)**: set `email` (Atlassian account email), `api_token` (minted at id.atlassian.com, sealed as a secret), and `base_url` (the site origin, e.g. `https://acme.atlassian.net`, no trailing path). `base_url` is required for this path only; the OAuth path resolves its base from the token.

Both paths require `project_key` (e.g. `ENG`).

## Required scopes / permissions

The OAuth flow requests the minimal granular scope set the sync needs (granular, not classic; classic scopes fail consent on a modern app):

- `read:jql:jira` — `POST /rest/api/3/search/jql` (the JQL change feed)
- `read:issue:jira` — reading each issue's `summary`, `description`, and `security`
- `offline_access` — a rotating refresh token

Consent is all-or-nothing, so the request is kept to this core. The ACL endpoints each need their own granular scope, but the ACL resolvers fail closed on a missing-scope error, so omitting those scopes never crashes a sync (it under-shares org-wide grants, the safe side). An operator who wants full org-wide Jira ACLs should additionally enable `read:permission-scheme:jira`, `read:project-role:jira`, `read:user:jira`, and `read:group:jira` on the app.

## Permission model (ACL mapping)

Verified against the official docs on 2026-07-05. All ACLs are namespaced by the instance (`principal_ns = instance_id`), so two instances never collide.

For each issue, if the `security` field is present (any non-null value, including an empty object), the issue is restricted to that security level's members ONLY, never the wider project audience. Only a null/absent `security` field means the issue inherits the project's browse audience.

**Issue security level** (`GET /rest/api/3/issuesecurityschemes/level/member?levelId={id}`, paginated by `startAt`/`isLast`): members are mapped to `allow_users`/`allow_groups`. An empty or id-less security object is a restriction the connector cannot name, so it fails closed (grants no one) rather than widening. This members endpoint requires the Administer Jira global permission, which read credentials lack; on any HTTP error the issue fails closed (granted to no one) rather than falling back to the project audience, because over-sharing a security-restricted issue is a security bug.

**Project browse audience** (`GET /rest/api/3/project/{key}/permissionscheme?expand=permissions`): only `BROWSE_PROJECTS` grants are read, resolved from the scheme's holders (not a hardcoded role id). Holder mapping:

- `group` → `allow_groups` (by group name)
- `user` → `allow_users` (by accountId)
- `projectRole` → the role's actors, via `GET /rest/api/3/project/{key}/role/{id}`
- `applicationRole` / `anyone` / `loggedin` ("any logged in user") → the reserved membership group `__jira_licensed_users`, NEVER `public`. "Any logged in user" means principals holding an Atlassian product license on this site, strictly narrower than every principal in the pansoph tenant, so mapping it to public would over-share to unlicensed accounts. The identity layer materializes this group to the licensed set; until then it grants no one (under-shares, safe).
- Dynamic holders that cannot resolve to fixed principals (reporter, assignee, project lead, group/user custom fields, service desk customer) are skipped, which under-shares and never widens.

If the permission scheme itself cannot be read, the browse audience also fails closed. The connector never sets `public=True`.

## Sync

The change feed is a JQL poll: `project = {key} ORDER BY updated ASC` via `POST /rest/api/3/search/jql`, using opaque `nextPageToken` pagination (stop when `isLast` is true or the token is absent; there is no total count).

Deletions are detected by client-side reconciliation, because a deleted issue simply disappears from JQL results. `list_changes` encodes the set of issue keys seen this run into the durable cursor (base64 JSON, always non-empty); on the next run any key present before but absent now is surfaced as a tombstone `DocRef` (`deleted=True`), so the runtime purges the document and its access tuples. A key can vanish because it was deleted or because the credential lost visibility (e.g. a newly applied security level). A malformed prior cursor is treated as empty (under-reports deletions, never fabricates one).

The webhook path (`on_webhook`) additionally translates the real-time `jira:issue_deleted` event into a delete, and `jira:issue_created` / `jira:issue_updated` into an upsert (which re-fetches and recomputes the ACL, so a newly applied security level propagates).

## Known limits

- Incremental sync is not claimed (`incremental=False`). The durable cursor carries the reconciliation key set, not a high-water mark, so a full re-list runs every sync. This is deliberate: a full re-list is what lets reconciliation catch a removed or newly restricted issue and re-import every ACL, serving the permission guarantee over sync efficiency.
- Resolving issue security level members requires the Administer Jira global permission, which read credentials (OAuth or a non-admin API token) lack. Security-restricted issues therefore fail closed (granted to no one) under read-only credentials.
- The old `GET`/`POST /rest/api/3/search` endpoint is removed (410 Gone); this connector uses `POST /rest/api/3/search/jql`.

## Conformance

This connector passes the shared conformance suite (`assert_conforms` from `pansoph-connector-sdk`), run hermetically with `uv run pytest -m conformance`.

## References

Implemented against the Jira Cloud platform REST API v3. Endpoints, pagination, permission model, and deletion signal verified against the official docs on 2026-07-05 (OAuth scopes re-verified 2026-07-19).

- [REST API v3 intro](https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/)
- [Issue search (`/rest/api/3/search/jql`)](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/#api-rest-api-3-search-jql-post)
- [Get issue](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/#api-rest-api-3-issue-issueidorkey-get)
- [Project permission schemes](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-project-permission-schemes/#api-rest-api-3-project-projectkeyorid-permissionscheme-get)
- [Permission scheme holders](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-permission-schemes/)
- [Project role actors](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-project-role-actors/#api-rest-api-3-project-projectidorkey-role-id-get)
- [Issue security level members](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-security-level/)
- [OAuth 2.0 (3LO) apps](https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/)
- [Scopes for OAuth 2.0 (3LO) and Forge apps](https://developer.atlassian.com/cloud/jira/platform/scopes-for-oauth-2-3LO-and-forge-apps/)
- [Webhooks](https://developer.atlassian.com/cloud/jira/platform/webhooks/)
