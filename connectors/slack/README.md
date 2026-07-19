# pansoph-connector-slack

A pansoph connector that ingests Slack channels and their messages, mapping each channel's Slack membership to a pansoph document ACL. It is implemented against the official Slack Web API and fails closed: a channel the credential cannot read never degrades into a public or empty grant.

## What it syncs

A document is a Slack channel. The connector lists every channel the credential can reach via `conversations.list` (requesting `types=public_channel,private_channel`) and reads its messages via `conversations.history`, joining message `text` fields into the document body. Both public and private channels are covered (`channels:*` scopes reach public channels, `groups:*` scopes reach private channels). The optional `channel_ids` config restricts ingestion to a chosen subset picked from the resource list.

## Setup

Authentication uses Slack OAuth v2 (the "installing with OAuth" flow). To set it up:

1. Create a Slack app and add the bot scopes listed below.
2. Register your pansoph callback as the app's OAuth Redirect URL. The authorize endpoint is `https://slack.com/oauth/v2/authorize` and the token exchange endpoint is `https://slack.com/api/oauth.v2.access`.
3. Provide the app credentials via `PANSOPH_OAUTH_SLACK_CLIENT_ID` and `PANSOPH_OAUTH_SLACK_SECRET` (or store them under `_oauth/slack/*` in the secret store).

The connector uses a **bot token** (`xoxb-`). You can mint one through the in-app OAuth flow, or paste a bot token (or a user token, `xoxp-`) directly; either way it is sealed in the secret store. Bot scopes are sent as a comma-separated `scope` list; pansoph never requests user scopes.

Token rotation is opt-in per Slack app. When enabled, the token response also carries `expires_in` (12 hours) and a `refresh_token` (`xoxe-`). Refresh calls the same endpoint with `grant_type=refresh_token`. Refresh tokens are single-use and rotate, so the shared credential provider persists the new refresh token on each refresh.

## Required scopes / permissions

The bot read scopes (`SLACK_READ_SCOPES` in the OAuth provider, matching the connector spec):

- `channels:read` — list public channels
- `channels:history` — read public channel messages
- `channels:join` — auto-join public channels (see Sync)
- `groups:read` — list private channels
- `groups:history` — read private channel messages
- `users:read` — back the membership ACL lookup

## Permission model (ACL mapping)

The ACL for a channel is derived from `conversations.info` and `conversations.members`, in the connector's own principal namespace (`principal_ns = instance_id`).

- **Private channel** (`is_private` true, also `im`/`mpim`): the ACL is the set of member user ids from `conversations.members`, paginated. These users become the readers (`allow_users`).
- **Public channel** (`is_private` false): does **not** map to `public=True`. A public channel is not world-readable in Slack (single/multi-channel guests must be explicitly added to a channel), and pansoph's public grant resolves to the tenant-everyone group, which can include principals with no Slack membership at all. Mapping public would over-share. Instead it maps to the namespaced full-member group `slack:member:{instance_id}` (`allow_groups`). Until the identity layer materializes that group's membership, the grant reaches no one, which under-shares (safe) rather than over-shares (a leak).
- Slack exposes no per-channel usergroups and no per-channel deny, so `deny` is always empty and `allow_groups` otherwise carries only that single membership group.
- **Fail-closed behavior:** every Slack call goes through an `ok`-envelope check. Slack returns HTTP 200 with `{"ok": false, "error": ...}` on application errors (`channel_not_found`, `missing_scope`, `not_in_channel`, `invalid_auth`, `token_revoked`), so a plain HTTP status check is not enough. On `ok=false` for the info or members lookup, the ACL builder **raises** rather than producing a public or empty grant. A private channel the bot was never invited to returns `not_in_channel` and must fail closed. `is_private` defaults to `True` (the narrower audience) when absent, so the one path that could emit a broad grant fails closed.

## Sync

- **Pagination** is cursor-based throughout: `conversations.list` and `conversations.members` pass `limit` (200, the doc-recommended page size), read `response_metadata.next_cursor`, re-pass it as `cursor`, and stop when the cursor is empty.
- **Auto-join** (`auto_join`, on by default): a public channel the bot is not yet a member of is best-effort joined via `conversations.join` before indexing, so a freshly installed bot works without a manual invite. This needs `channels:join`; without it the join fails and the channel is skipped (never fatal). Private channels are never self-joined and must be invited.
- **Incremental sync is off** (`incremental=False`). `list_changes` walks the full `conversations.list` every run. There is no workspace-level change feed, so a since-token backfill is a follow-up. The cursor yielded per ref is a non-empty page-level list token, not a cross-run resume point.
- **Deletions are off** (`deletions=False`). Slack exposes real deletion signals (`channel_deleted`, `group_deleted`, `message_deleted`), but the connector does not yet consume them: `list_changes` never yields a tombstone and `on_webhook` emits only upserts.
- **Webhooks:** an incoming Slack `message` event upserts the channel document it belongs to. `permission_sync` is on and backed by a real `list_acls`.

## Known limits

- No incremental sync: every run re-lists the full workspace (no change-feed backfill yet).
- No deletion tombstones: deleted channels and messages are not propagated (webhook emits upserts only).
- Public channel ACLs grant no one until the identity layer materializes the `slack:member` group's membership (safe under-share, not a leak).
- Rate limits (HTTP 429) are surfaced by raising with the `Retry-After` seconds; the runtime owns retry and backoff, the connector does not sleep or retry itself.

## Conformance

This connector passes the shared conformance suite (`assert_conforms` from `pansoph-connector-sdk`), run hermetically with `uv run pytest -m conformance`.

## References

Slack Web API (versionless; methods under `https://slack.com/api/`). Verified 2026-07-05 (connector) and 2026-07-04 (OAuth provider).

- Web API overview: https://docs.slack.dev/apis/web-api/
- Conversations API: https://docs.slack.dev/apis/web-api/using-the-conversations-api/
- `conversations.list`: https://docs.slack.dev/reference/methods/conversations.list/
- `conversations.history`: https://docs.slack.dev/reference/methods/conversations.history/
- `conversations.info`: https://docs.slack.dev/reference/methods/conversations.info/
- `conversations.members`: https://docs.slack.dev/reference/methods/conversations.members/
- Cursor pagination: https://docs.slack.dev/apis/web-api/pagination
- Rate limits: https://docs.slack.dev/apis/web-api/rate-limits/
- Token types: https://docs.slack.dev/authentication/tokens
- Installing with OAuth (v2): https://docs.slack.dev/authentication/installing-with-oauth
- `oauth.v2.access`: https://api.slack.com/methods/oauth.v2.access
- Token rotation: https://docs.slack.dev/authentication/using-token-rotation
- Scopes reference: https://docs.slack.dev/reference/scopes
- Guest roles (ACL rationale): https://slack.com/help/articles/202518103-Guest-roles-in-Slack
