# pansoph-connector-teams

A pansoph connector for Microsoft Teams, built on the Microsoft Graph v1.0 API. It ingests a team's channel messages as documents and maps each channel's membership to a pansoph ACL, so retrieval only ever returns a channel to a user who can actually see it.

## What it syncs

- A document is a **channel**. `list_changes` lists every channel in the configured team via `/v1.0/teams/{team_id}/channels`, paginated by `@odata.nextLink`.
- `fetch` reads that channel's messages via `/v1.0/teams/{team_id}/channels/{channel_id}/messages` and joins each message's HTML `body.content` into one `text/html` document.
- Teams themselves are not enumerated: you configure one team per instance (`team_id`).

## Setup

Register an app in Microsoft Entra ID (Azure AD) and grant it Microsoft Graph permissions for the tenant. The operator acquires an **app-only OAuth2 access token** for that app registration through the standard client-credentials flow (redirect/callback URLs are part of your Entra app registration, not the connector; the connector never mints or refreshes tokens). Paste the resulting bearer token into the instance config.

| Key | Required | Description |
|---|---|---|
| `token` | yes | Microsoft Graph app-only OAuth2 access token. Sent as `Authorization: Bearer <token>`. Sealed in the secret store (`FieldKind.SECRET`). |
| `team_id` | yes | The Entra group id of the team whose channels to ingest (e.g. `19:abc123...@thread.tacv2` or the team GUID). |
| `base_url` | no | Graph base URL, default `https://graph.microsoft.com`. Override only for a national cloud or a proxy gateway. Versioned paths (`/v1.0/...`) are added by the connector. |

Each configured instance carries its own credential and its own ACL namespace (`principal_ns = instance_id`), so instances scoped to different tenants never collide.

## Required scopes / permissions

The connector does not request scopes itself; the operator provisions the app registration so the supplied token carries at least:

- `ChannelMessage.Read.All` (or equivalent channel message read permission) to read channel messages.
- `ChannelMember.Read.Group` or `ChannelMember.Read.All` to list channel members (`allMembers`).
- `TeamMember.Read.All` to list the team roster that standard channels inherit.

Because access is RBAC controlled, the emitted ACL reflects the credential's actual reach, not an assumption.

## Permission model (ACL mapping)

The channel ACL is the set of users who can actually see the channel, and the faithful source **depends on the channel's `membershipType`**, because Graph's `channel/members` endpoint returns only DIRECT members for every channel type. Getting this wrong is a security bug, so the connector reads `membershipType` first (`/v1.0/teams/{team_id}/channels/{channel_id}`) and then chooses the source:

- **standard**: membership is inherited from the parent team, not stored on the channel. The connector reads the team roster at `/v1.0/teams/{team_id}/members`. Using `channel/members` here would under-share (omit team members who can read the channel).
- **private / shared** (and any unrecognized or absent value): the connector reads `/v1.0/teams/{team_id}/channels/{channel_id}/allMembers`, which returns direct AND indirect members. This is the safe superset and never under-shares relative to `channel/members`.

Each `conversationMember`'s `userId` (the Entra object id) becomes an `allow_user`. Members without a `userId` (e.g. an anonymous guest) are skipped, since they carry no principal to map. A shared channel's `allMembers` can list the same user more than once, so the user list is deduplicated (first-seen order preserved).

Fail-closed behavior: mapping is `allow_users` only, at the `userId` granularity Graph exposes. No `allow_groups` and no deny are emitted (deny semantics do not exist in this Teams access model). Group-backed access is not expanded here; the `userId` lists already name the concrete principals.

## Sync

- **Pagination**: Graph returns a `value` array per page plus an absolute `@odata.nextLink`. The connector follows `nextLink` until it is absent.
- **Incremental**: not implemented in this first cut. `Capabilities.incremental` is `False`, so the runtime performs full listings and `next_cursor` stays the empty string. (Graph's `chatMessage` delta query can raise this to `True` honestly in a later revision.)
- **Deletions**: not implemented. `Capabilities.deletions` is `False`; `list_changes` never yields a tombstone, so the flag is honest.
- **Webhooks**: Graph change notifications are not wired up; `on_webhook` returns an empty list.
- **Rate limits**: Graph throttles per app and per tenant and returns `429` with `Retry-After`. Retries and backoff are owned by the runtime, so each connector method describes only one page or one document.

## Known limits

- No incremental sync, no deletions, and no webhook change notifications in this first cut (see Sync). Every run performs a full listing of channels.
- Group-backed channel access is not expanded to `allow_groups`; access is mapped at the `userId` level only.

## Conformance

This connector passes the shared conformance suite (`assert_conforms` from `pansoph-connector-sdk`), run hermetically with `uv run pytest -m conformance`.

## References

Implemented and verified against the official Microsoft Graph v1.0 reference on **2026-07-05** (channel resource, list channels, list channel messages, list members, list allMembers, and list team members):

- List channels: https://learn.microsoft.com/graph/api/channel-list?view=graph-rest-1.0
- Channel resource (`membershipType`: standard, private, shared): https://learn.microsoft.com/graph/api/resources/channel?view=graph-rest-1.0
- List channel messages: https://learn.microsoft.com/graph/api/channel-list-messages?view=graph-rest-1.0
- List channel members (direct only): https://learn.microsoft.com/graph/api/channel-list-members?view=graph-rest-1.0
- List allMembers (direct and indirect): https://learn.microsoft.com/graph/api/channel-list-allmembers?view=graph-rest-1.0
- List team members (roster a standard channel inherits): https://learn.microsoft.com/graph/api/team-list-members?view=graph-rest-1.0
- conversationMember resource (`userId`): https://learn.microsoft.com/graph/api/resources/conversationmember?view=graph-rest-1.0

See the module docstring in `src/pansoph_connector_teams/connector.py` for the full verification checklist.
