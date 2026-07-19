# pansoph-connector-notion

A pansoph connector for Notion. It ingests pages shared with a Notion integration, reads their block content as plain text, and maps Notion's connection scope to a fail-closed pansoph ACL. Implemented against the official Notion API, `Notion-Version 2025-09-03`, verified 2026-07-05. See the header of `src/pansoph_connector_notion/connector.py` for the endpoint-by-endpoint provenance.

## What it syncs

- `list_changes` pages through `POST /v1/search`, filtered to `object=page`, yielding one `DocRef` per shared page. It follows Notion's intra-listing `next_cursor` to the end, so one call returns the complete listing.
- `fetch` reads `GET /v1/blocks/{id}/children` and joins the `plain_text` of every block's `rich_text` spans into a `text/plain` document.
- Databases are reachable only in that pages shared through a database appear in search results. There is no dedicated database-schema or property extraction; content is block text only.

## Setup

1. Create a Notion internal integration at https://www.notion.so/my-integrations and copy its integration token.
2. Share the pages and databases you want to ingest with that integration (open the page, use the share/connections menu, add the integration). Notion only exposes shared objects to the connection, so this share step defines the sync scope.
3. Configure a pansoph instance of this connector. The connector needs one secret, `token`, which carries the internal integration token. An OAuth access token works in the same field; both are sent as `Authorization: Bearer <token>`.

Config fields (from `spec()`):

```json
{
  "token": "<notion integration or oauth token>",
  "base_url": "https://api.notion.com",
  "public": false,
  "integration_principal": ""
}
```

- `token` (secret, required): sealed in the secret store.
- `base_url` (string, optional): override only for a proxy or self-managed gateway. Defaults to `https://api.notion.com`.
- `public` (bool, optional): audited ACL override, default `false`. See below.
- `integration_principal` (string, optional): override for the ACL principal id.

## Required scopes / permissions

The integration needs read capability on the shared content: read page content, and read user information (for `GET /v1/users/me`, used to resolve the ACL principal). A Notion integration behaves like a restricted user, so its capability set and the pages shared with it together bound what the connector can see. The connector performs read-only calls (`POST /v1/search`, `GET /v1/blocks/{id}/children`, `GET /v1/users/me`); no write or content capabilities are required.

## Permission model (ACL mapping)

Security-critical, exact from the code.

A Notion integration behaves like a restricted user: `POST /v1/search` returns only pages shared with the connection. So the integration's reachable set is a real, observable permission signal, and what the connector can see is exactly what search returns.

Notion exposes no per-page "which principals can see this" endpoint, so the connector emits the faithful minimal ACL: each ingested page is readable only by this instance's integration principal, namespaced by the instance (`principal_ns = instance_id`). The principal id resolves in this order: config `integration_principal`, then the bot id from `GET /v1/users/me` (cached), then the instance id as a last resort. If the `/v1/users/me` lookup fails, the connector keeps the instance-id principal and never widens the grant. `capabilities.permission_sync` is `True`; `list_acls` returns this grant.

Fail-closed behavior:
- A stale index entry carries only the integration-scoped grant (`allow_users=[principal]`), which no end user is, so it under-shares rather than leaks.
- A live re-fetch of a trashed or un-shared page returns `404`/`403`, surfaced by `_raise_for_notion`, so revoked content is never served.
- `public` is an explicit, audited operator override, default `false`. It never defaults wider than the integration scope. Setting it `true` deliberately publishes every ingested page to every user in the instance, so scope the integration accordingly.

On Business/Enterprise, page-level access rules via `Person`/`Created-by` properties plus `GET /v1/users` could enrich this to real per-page principals. That is a documented follow-up, not a current feature.

## Sync

- Change feed: full listing each run via `POST /v1/search`. Incremental is `False`; every yielded ref carries a stable full-scan sentinel cursor (`notion:full-scan`) rather than reusing Notion's intra-listing `next_cursor`, which the contract forbids across runs.
- Cursor replay: a run that resumes from the sentinel is a no-op replay, since the prior run already returned the complete listing.
- Pagination: cursor based. `POST /v1/search` and `GET /v1/blocks/{id}/children` return `has_more` + `next_cursor`; the connector passes `start_cursor` to continue internally.
- Deletions/archived: `deletions` is `False`, and honestly so. `POST /v1/search` excludes trashed pages, so a removed or un-shared page simply stops appearing; the API never returns a page carrying `in_trash=True`, and there is no bulk deletion feed. The runtime purges removals by reconciling the full-scan listing against the previously synced set (search-absence is the removal signal), not a tombstone stream.
- Rate limits: ~3 req/s per connection; `429` carries `Retry-After`. The connector surfaces the `429` code and lets the runtime own backoff.

## Known limits

- No incremental high-water mark; each run is a full scan. A `last_edited_time` sort/filter is a documented follow-up.
- No tombstone/deletion feed (see Sync above); removals are inferred by reconciliation.
- Content extraction is block `plain_text` only; database schemas, page properties, and non-text block payloads are not extracted.
- `on_webhook` accepts events but returns no change events.
- ACL is integration-principal granularity; real per-page principals on Business/Enterprise are not yet mapped.

## Conformance

This connector passes the shared conformance suite (`assert_conforms` from `pansoph-connector-sdk`), run hermetically with `uv run pytest -m conformance`.

## References

- Notion API intro and auth: https://developers.notion.com/reference/intro
- Search (list shared pages): https://developers.notion.com/reference/post-search
- Block children (page content): https://developers.notion.com/reference/get-block-children
- Rich text (`plain_text`): https://developers.notion.com/reference/rich-text
- Bot user (ACL principal): https://developers.notion.com/reference/get-self
- Users: https://developers.notion.com/reference/get-user
- Errors: https://developers.notion.com/reference/errors
- Request limits: https://developers.notion.com/reference/request-limits
- Versioning: https://developers.notion.com/reference/versioning
- Trash semantics: https://developers.notion.com/reference/trash-page
- 2025-09-03 data_sources model: https://developers.notion.com/docs/upgrade-faqs-2025-09-03

API version: `Notion-Version 2025-09-03` (mandatory header on every request). Verified 2026-07-05.
