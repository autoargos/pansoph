# pansoph-connector-confluence

A pansoph connector for Confluence Cloud. It syncs pages (body, title, source URL) with faithful VIEW permissions, using the Confluence Cloud REST API v2 for content, discovery, and space permissions, and two v1 endpoints (CQL search and read restrictions) that still accept a granular OAuth token. `src/pansoph_connector_confluence/connector.py` is the authoritative implementation; its module header records the verified doc links and API version.

## What it syncs

- Pages: body (v2 `body-format=storage`, emitted as `text/html`), title, and a human-openable source URL built from the page's `_links`.
- Spaces: enumerated for the space picker via `GET /wiki/api/v2/spaces`. Each resource id is the space key; the numeric v2 id is carried in `extra`.
- Multi-space selection: the `space_key` field is multi-select (a list of keys), so an instance can be confined to one or more spaces. A legacy scalar is still accepted. Blank means every reachable space.
- ACLs: per-page VIEW audience, materialized to concrete principals (see Permission model).

## Setup

Two auth paths are supported, both read-only.

1. OAuth 2.0 (3LO), the in-app Connect flow. Create an OAuth 2.0 (3LO) app in the Atlassian developer console (https://developer.atlassian.com/console/myapps/), enable the granular scopes below, and set the redirect/callback URL to your pansoph OAuth callback for provider `atlassian` (the pattern `<pansoph-base>/api/oauth/atlassian/callback`). The client credentials are supplied to pansoph as the secrets `PANSOPH_OAUTH_ATLASSIAN_CLIENT_ID` and `PANSOPH_OAUTH_ATLASSIAN_CLIENT_SECRET`. Consent mints a Bearer token sealed into the config key `oauth_token`.

   A 3LO token is not bound to a site origin, so the connector resolves the Atlassian Cloud id itself from the token via `GET https://api.atlassian.com/oauth/token/accessible-resources`, then addresses the API at `https://api.atlassian.com/ex/confluence/{cloud_id}`. An OAuth instance therefore needs no `base_url`. If the grant reaches multiple sites, disambiguate with the optional `cloud_id` or `site_url` config fields; otherwise the first accessible site is used.

2. HTTP Basic (manual paste). Set `email` (Atlassian account email) and `token` (an API token minted at https://id.atlassian.com/manage-profile/security/api-tokens), plus the site `base_url` (`https://your-domain.atlassian.net`). The connector sends `Authorization: Basic base64("{email}:{api_token}")`.

Granular vs classic scopes: the connector requests both the granular `read:page:confluence` and the classic `read:confluence-content.all`, so it works against a granular app and a classic-only app alike; Atlassian grants whichever the app has enabled. Once any granular scope is present, Atlassian evaluates content reads in granular mode, which is why content is read only through v2.

Set the `space_key` field (multi-select) to limit ingestion to specific spaces, or leave it blank for all reachable pages.

## Required scopes / permissions

The OAuth 2.0 (3LO) flow requests these scopes:

- `search:confluence` (CQL change feed, `GET /wiki/rest/api/content/search`)
- `read:page:confluence` (page content and trashed reads via v2, granular)
- `read:confluence-content.all` (classic equivalent, for a classic-only app)
- `read:confluence-space.summary` (space info)
- `read:content.restriction:confluence` (a page's read restrictions, the ACL)
- `read:space:confluence` (space discovery, `GET /wiki/api/v2/spaces`)
- `read:space.permission:confluence` (space View audience, `GET /wiki/api/v2/spaces/{id}/permissions`)
- `offline_access` (rotating refresh token)

## Permission model (ACL mapping)

This is the security-critical part. Confluence VIEW is a conjunction of independent gates: to view a page a user must satisfy the page's own read restriction, AND every ancestor's read restriction (restrictions cascade to descendants), AND hold the space View permission. A page restriction can only narrow access within the space, never grant beyond it.

pansoph's `AccessControl` is a pure union model, so it cannot express an AND directly. The connector therefore materializes the conjunction to concrete principals at import time and always maps to the narrower side:

- It walks the page's `parentId` chain (v2 pages carry only the immediate parent) and reads each ancestor's direct read restriction from `GET /wiki/rest/api/content/{id}/restriction/byOperation/read`. The content-restrictions API never returns inherited restrictions, so the cascade is reconstructed layer by layer.
- It reads the space View audience (principals holding the space `read` permission where `targetType == "space"`) from `GET /wiki/api/v2/spaces/{id}/permissions`.
- The emitted audience is the per-axis intersection (`allow_users` and `allow_groups` separately) across every restriction layer AND the space View audience. A restriction principal that does not also hold space View is dropped, so a page restricted to a broad group inside a narrow space resolves to the narrow space, never the broad group. This intersection is a subset of the true effective audience, so it never widens a grant.
- Only when the page and every ancestor are confirmed unrestricted (the read-restriction expansion is present and names no principals) does the audience equal the space View audience alone.
- Users map to `allow_users` by `accountId`, groups to `allow_groups` by the stable `groupId` (falling back to name only when id is absent, so a layer is never mistaken for unrestricted). Role (RBAC) principals map to a namespaced `space-role:{id}` group the authz layer materializes; an unmaterialized role grants no one.

Fail-closed: `byOperation/read` answers `200` with empty result sets for an unrestricted page, so "unrestricted" is a positive signal, not an absence. If any restriction, ancestor, or space-permission read returns a non-2xx, the page collapses to an empty audience that grants no one. Under-sharing is a UX bug; over-sharing is a security bug. There is no deny concept in Confluence view restrictions. Each instance uses its own credential and its own ACL namespace (`principal_ns = instance_id`), so instances never collide.

## Sync

- Incremental upserts use a real high-water-mark cursor over the CQL `lastmodified` field. The connector queries `GET /wiki/rest/api/content/search` with `type=page [AND space in ("KEY", ...)] AND lastmodified >= "<floor>" ORDER BY lastmodified ASC`, expanding only `version`. CQL `lastmodified` has minute granularity, so the query floor is the cursor truncated to the minute (a safe over-fetch), and the connector then filters precisely on `version.when` (full ISO-8601), skipping any page at or before the exact cursor. Each yielded cursor is that page's `version.when`, so a crash resumes at the last checkpointed page and a replay yields no already-seen page.
- Pagination follows the opaque cursor extracted from the relative `_links.next` URL, across content search, spaces, and space permissions.
- Deletions: CQL cannot search trash, so trashed pages are a separate tombstone phase. The connector polls `GET /wiki/api/v2/pages?status=trashed` (scoped to each configured space by numeric space-id when set) and yields a `DocRef(deleted=True)` per trashed page. Tombstones carry the current upsert checkpoint so the persisted cursor stays a valid `lastmodified` bound.

## Known limits

- No webhook support: `on_webhook` returns an empty list, so sync is poll-based.
- CQL `lastmodified` has only minute granularity, so the incremental floor over-fetches (corrected by the precise `version.when` filter in code).
- When restriction layers or the space audience name different identifier axes, the intersection can under-share (a correctness gap, deliberately chosen as the safe side, never a leak).
- Two v1 endpoints remain in use (CQL search and `restriction/byOperation/read`) because v2 has no parity for them yet; Atlassian is deprecating v1 endpoints that have v2 equivalents, so these should migrate once v2 exposes a CQL/lastmodified search and a content-restriction read.

## Conformance

This connector passes the shared conformance suite (`assert_conforms` from `pansoph-connector-sdk`), run hermetically with `uv run pytest -m conformance`.

## References

Confluence Cloud REST API, verified 2026-07-06 against the official Atlassian developer docs (v2 for content, discovery, and space permissions; v1 for CQL search and read restrictions):

- Content and CQL search (v1): https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-content/#api-wiki-rest-api-content-search-get
- Content restrictions read (v1): https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-content-restrictions/
- CQL reference: https://developer.atlassian.com/cloud/confluence/advanced-searching-using-cql/
- Pages and spaces (v2): https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-space/#api-spaces-get
- Space permissions (v2): https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-space-permissions/
- v2 pagination: https://developer.atlassian.com/cloud/confluence/rest/v2/intro/#pagination
- OAuth 2.0 (3LO): https://developer.atlassian.com/cloud/confluence/oauth-2-3lo-apps/
- Scopes for OAuth 2.0 (3LO) and Forge apps: https://developer.atlassian.com/cloud/confluence/scopes-for-oauth-2-3LO-and-forge-apps/
