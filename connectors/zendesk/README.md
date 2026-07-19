# pansoph-connector-zendesk

A pansoph connector for Zendesk Help Center articles. It ingests published articles as documents and maps each article's Zendesk user segment to a pansoph `AccessControl`, so retrieval only ever returns an article to a principal the segment actually grants. Implemented against the official Zendesk Help Center API, verified 2026-07-05.

## What it syncs

Help Center articles, listed per locale from `/api/v2/help_center/{locale}/articles.json` and fetched individually from `/api/v2/help_center/articles/{id}.json`. Each document is emitted as `text/html` (the article `body`) with its `title` and `html_url`. Draft (unpublished) translations are skipped in both listing and fetch, so internal content is never indexed. This connector syncs articles only; it does not sync tickets.

## Setup

Auth is a Zendesk API token used as HTTP Basic, per the Zendesk security and auth doc. The username is `{email}/token` and the password is the API token; the pair is base64-encoded into an `Authorization: Basic <b64>` header. The token belongs to an agent or admin. There is no OAuth flow and no redirect/callback URL in this connector.

Config fields (from `ZendeskConnector.spec()`):

| Key | Required | Kind | Notes |
|---|---|---|---|
| `subdomain` | yes* | string | Account subdomain in `{subdomain}.zendesk.com`. Required unless `base_url` is set. |
| `email` | yes | string | Agent email; sent as `{email}/token` for Basic auth. |
| `token` | yes | secret | Zendesk API token. Sealed in the secret store, never stored as config. |
| `locale` | no | string | Help Center locale for listing. Defaults to `en-us`. |
| `base_url` | no | string | Override the host built from the subdomain (proxy or self-managed gateway). |

*`subdomain` is required only when `base_url` is not set. The base URL is otherwise built as `https://{subdomain}.zendesk.com`. `validate()` fails closed if `email` or `token` is missing (and `subdomain` unless `base_url` is provided).

## Required scopes / permissions

The credential must be an agent-or-admin API token for the account. With agent Basic auth, draft (unpublished) translations are visible to the credential (unlike OAuth, which returns only published content), which is exactly why this connector skips drafts explicitly. The connector reads three endpoints: `GET /api/v2/help_center/{locale}/articles.json`, `GET /api/v2/help_center/articles/{id}.json`, and `GET /api/v2/help_center/user_segments/{id}.json`. No finer OAuth scope list applies; access is whatever the account and the agent/admin role behind the token can see.

## Permission model (ACL mapping)

Each article carries a `user_segment_id`. A null segment means public. A non-null segment is fetched from `/api/v2/help_center/user_segments/{id}.json` (cached per segment id for the run) and mapped to a pansoph `AccessControl` under `principal_ns = instance_id`. Zendesk segments are additive allow-lists, so the connector never emits deny entries.

Zendesk combines narrowing attribute types (`group_ids`, `organization_ids`, `or_tags`, `tags`) with AND across types and OR within a type. pansoph `AccessControl` is a pure OR over `allow_groups`, so the mapping counts how many narrowing categories a segment restricts on:

- **Null segment** -> `public = True`.
- **Zero narrowing categories** -> base audience marker only: `user_type == "staff"` -> `allow_groups = ["staff"]`; `user_type == "signed_in_users"` -> `allow_groups = ["signed_in"]`.
- **Exactly one narrowing category** (faithful OR): `group_ids` -> `group:{id}`; `organization_ids` -> `org:{id}`; `or_tags` -> `tag:{t}` (at least one).
- **`tags` (AND / conjunction)** -> **fail closed** with the `unsatisfiable:tags_and` sentinel. The OR resolver cannot express "must hold ALL tags", so mapping each tag would over-grant.
- **Two or more narrowing categories** (a real cross-category AND, including `staff` paired with an org or tag restriction) -> **fail closed** with the `unsatisfiable:cross_category_and` sentinel. Flattening to an OR would grant anyone matching either category (a leak); the sentinel grants no one until the identity layer materializes the true intersection.
- **`added_user_ids`** -> always unioned in as `allow_users = ["user:{u}", ...]`, even under a fail-closed grant, because the source grants these individuals "regardless of matching tags or other criteria".

Base audience markers are emitted only in the zero-narrowing case, so a restricted segment is never widened to all staff or all signed-in users. A draft article reaching `fetch`/`list_acls` returns the `unsatisfiable:tags_and` sentinel (fail closed) as defense in depth. The rule throughout: under-sharing is safe, over-sharing is a leak.

## Sync

`incremental=False`, honestly: `list_changes` does a full listing every run via cursor pagination (`meta.has_more` + `links.next`), not the incremental export `start_time` watermark. Help Center article exports carry no deletion signal (no tombstone, no deleted-id list), so deletions are detected by client-side reconciliation. The resume cursor is a base64-encoded JSON sorted list of the published article ids seen this run (always non-empty). Any id present in the previous run's cursor but absent now is surfaced as a `DocRef` with `deleted=True`. A malformed cursor is treated as empty (under-reports deletions rather than fabricating one). `on_webhook` returns no events; the runtime drives full-listing reconciliation. Capabilities: `permission_sync=True`, `deletions=True`, `slim_listing=True`.

## Known limits

- Not incremental: every run re-lists all articles (no `start_time` export watermark).
- Deletions are inferred by reconciliation, not a source deletion signal.
- Segments restricting on `tags` (AND) or two-plus narrowing categories fail closed to an unsatisfiable sentinel, so such articles are under-shared until the identity layer materializes the true principal set.
- Rate limits: Zendesk returns 429 with `Retry-After`; the runtime owns backoff, this connector surfaces it.

## Conformance

This connector passes the shared conformance suite (`assert_conforms` from `pansoph-connector-sdk`), run hermetically with `uv run pytest -m conformance`.

## References

Zendesk Help Center API, verified 2026-07-05 against the current official docs:

- Articles API: https://developer.zendesk.com/api-reference/help_center/help-center-api/articles/
- User Segments: https://developer.zendesk.com/api-reference/help_center/help-center-api/user_segments/
- Understanding incremental article exports: https://developer.zendesk.com/documentation/help_center/help-center-api/understanding-incremental-article-exports/
- Incremental Exports overview: https://developer.zendesk.com/api-reference/ticketing/ticket-management/incremental_exports/
- Pagination (cursor): https://developer.zendesk.com/api-reference/introduction/pagination/
- Security and auth (API token Basic): https://developer.zendesk.com/api-reference/introduction/security-and-auth/
- Creating user segments for help center user permissions: https://support.zendesk.com/hc/en-us/articles/4408837707290
