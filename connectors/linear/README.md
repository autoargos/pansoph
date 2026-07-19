# pansoph-connector-linear

A pansoph connector for [Linear](https://linear.app), implemented against the Linear GraphQL API. It ingests issues from a Linear workspace and maps each issue's access to a pansoph ACL derived from its team. Verified against the official docs on 2026-07-05.

## What it syncs

Issues. For each issue the connector fetches `title` and `description` and concatenates them into a single `text/plain` document (title as the doc title). It reads `team { id key private }` on each issue to derive access control. It does not sync Linear documents, comments, projects, or attachments.

## Setup

Configure one instance per credential. Each instance carries its own credential and its own ACL namespace (`principal_ns = instance_id`), so a personal key over one workspace and an OAuth token over an org never collide.

Config fields (from `LinearConnector.spec()`):

- `token` (secret, required): a Linear personal API key, or `Bearer <token>` for an OAuth access token. It is sent verbatim as the `Authorization` header and sealed in the secret store.
- `base_url` (string, optional): API host, defaults to `https://api.linear.app`. Override only for a proxy or self-managed gateway. The GraphQL path is always `/graphql`.

Auth detail: a Linear personal API key is sent WITHOUT a `Bearer ` prefix (`Authorization: <api_key>`). OAuth access tokens use the `Bearer ` prefix. Because the connector sends the credential verbatim, pass a personal key as is, or `Bearer <token>` for OAuth. `validate()` fails closed if `token` is missing.

## Required scopes / permissions

The connector code does not declare or request any explicit OAuth scope list. Access is whatever the supplied credential (personal API key or OAuth token) already grants over the workspace. The connector reads issues and their team's `private` flag; the credential must be able to read those.

## Permission model (ACL mapping)

Access follows Linear team membership, and it splits on `Team.private` (read from GraphQL, never assumed). This is security-critical and is implemented exactly as follows in `_acl`:

- **Private team**: only team members can see its issues. Maps to `allow_groups=[team_id]` in the instance namespace (the private team is the closed group). This is also the default when the `private` flag is absent, failing closed on the privacy axis.
- **Open (non-private) team**: visible to every FULL workspace member (Owner / Admin / Team Owner / Member) but NOT to guests. Linear guests are workspace principals who cannot see other teams' issues. pansoph's UNION-only `AccessControl` cannot express "all full members but not guests" as `public=True` without leaking to guest principals, so an open team maps to the reserved full-members group `linear:workspace-members`, never `public=True`.
- **Unresolvable team** (null/inaccessible/deleted issue, or a GraphQL partial error returning `data.issue = null` under HTTP 200): maps to `allow_groups=[]`, granting no one.

Fail-closed behavior: this connector never emits `public=True` for a Linear issue, because no Linear issue is visible to every workspace principal (guests are principals who are excluded). The `linear:workspace-members` group under-shares (grants no one) until the identity layer materializes it with the workspace's full members excluding guests, which is the safe direction. Linear exposes no per-issue viewer list, so there are no per-issue `allow_users` and no issue-level deny.

## Sync

Full re-list each run (this connector is not incremental).

- **Endpoint**: single GraphQL endpoint `POST /graphql`, JSON body `{"query": ..., "variables": ...}`.
- **Pagination**: GraphQL connections expose `pageInfo { hasNextPage endCursor }`. The listing query pages `issues(first: 100, after: $after, orderBy: updatedAt)`, passing `endCursor` as `after` until `hasNextPage` is false.
- **Per-doc cursor**: each issue yields its `updatedAt` as a durable, non-empty resume token (falling back to the issue `id` if `updatedAt` is absent).
- **Incremental**: not supported (`Capabilities.incremental = False`). The incoming cursor is ignored and paging restarts at the head every run.
- **Deletions**: not supported (`Capabilities.deletions = False`). A full re-list cannot observe a hard delete, so the connector never emits a `deleted=True` DocRef. `on_webhook` is a stub returning `[]`.

Declared capabilities: `incremental=False`, `permission_sync=True`, `deletions=False`, `slim_listing=True`.

## Known limits

- Not incremental: every run re-lists the full issue set. The future path noted in the code is `filter:{updatedAt:{gt: <cursor>}}` using Linear's `IssueFilter` date comparators.
- No deletion tombstones: hard deletes are invisible to a full re-list. The future path is wiring `on_webhook` to translate the Linear `remove` webhook action into tombstones.
- The `linear:workspace-members` group grants no one until the identity layer materializes it with the workspace's full members (excluding guests).
- Only issues are synced (title plus description); no comments, projects, documents, or attachments.

## Conformance

This connector passes the shared conformance suite (`assert_conforms` from `pansoph-connector-sdk`), run hermetically with `uv run pytest -m conformance`.

## References

- Linear GraphQL API: https://linear.app/developers/graphql
- Pagination: https://linear.app/developers/pagination
- Filtering: https://linear.app/developers/filtering
- Private teams: https://linear.app/docs/private-teams
- Member roles: https://linear.app/docs/members-roles
- Guest accounts: https://linear.app/changelog/2022-07-14-guest-accounts
- Webhooks: https://linear.app/developers/webhooks and https://linear.app/docs/api-and-webhooks
- Endpoint: `https://api.linear.app/graphql`. Verified on 2026-07-05.
