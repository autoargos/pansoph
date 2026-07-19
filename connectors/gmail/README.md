# pansoph-connector-gmail

A pansoph connector for a single Gmail mailbox. It ingests messages over the Gmail API (REST v1) using an OAuth 2.0 bearer access token, syncs incrementally via the Gmail history API, and maps every message to an owner-only ACL so a document is only ever retrievable by the mailbox owner.

## What it syncs

Individual Gmail messages. `list_changes` enumerates message ids (`users.messages.list`), and `fetch` retrieves each message with `users.messages.get?format=full`, extracting the `Subject` header as the title and Gmail's own `snippet` (a short plain text excerpt of the body) as the text. A richer body parse would walk `payload.parts`; today only the snippet is stored.

## Setup

1. In the Google Cloud console, create an OAuth 2.0 client (web application) and enable the Gmail API.
2. Register the pansoph OAuth callback as an authorized redirect URI (the in-app Google OAuth flow uses `access_type=offline`, `prompt=consent` to mint and seal an access token plus a refresh token).
3. Provide the Google client credentials to pansoph via the OAuth secret keys `PANSOPH_OAUTH_GOOGLE_CLIENT_ID` and `PANSOPH_OAUTH_GOOGLE_CLIENT_SECRET`.
4. Configure a connector instance with these fields:
   - `token` (OAuth, provider `google`): connected through the in-app Google flow, or a current bearer access token pasted by hand. Sealed in the secret store and sent as `Authorization: Bearer <token>` on every request.
   - `user_email`: the mailbox owner (for example `alice@example.com`). Used as the single ACL principal. The API path itself uses the special userId `me` to address the authenticated mailbox.
   - `base_url` (optional): defaults to `https://gmail.googleapis.com`. Override only for a proxy or self-managed gateway.
   - `max_results` (optional): messages requested per list page (Gmail `maxResults`), default `100`.

## Required scopes / permissions

The connector requests exactly one OAuth scope, read only and whole-mailbox:

- `https://www.googleapis.com/auth/gmail.readonly`

No delegation or sharing scope is requested.

## Permission model (ACL mapping)

A Gmail mailbox is private to its owner. Under the `gmail.readonly` scope this connector requests, Gmail exposes no per-message sharing surface and no reachable additional principals: access is whole-mailbox and gated by the OAuth scope the owner granted. So every message maps to an `AccessControl` that:

- allows exactly the owner (config `user_email`) as `allow_users`,
- is not public (`public=False`), with no groups and no deny,
- is namespaced by the instance (`principal_ns = instance_id`), so two instances over two mailboxes never collide.

This is coarse but faithful: it never overshares, because the owner is the only principal the credential can reach. `list_acls` needs no HTTP call; the ACL is fully determined by config.

Scope caveat (not a bug at `gmail.readonly`): the only Gmail sharing surface is coarse whole-mailbox delegation (`settings.delegates`), gated behind the separate `gmail.settings.sharing` scope this connector does not request. If a future instance widens the credential to include `gmail.settings.sharing`, the ACL mapping must also enumerate `users.settings.delegates` as `allow_users`, or it would under-share (over-deny) for that scope. Until then, owner-only is the faithful mapping.

## Sync

- **Initial backfill:** with no cursor, `list_changes` captures the mailbox's current `historyId` from `users.getProfile` up front, then pages `users.messages.list` (following `nextPageToken` until absent), yielding a live `DocRef` for each message id carrying that historyId as the durable cursor.
- **Incremental:** with a cursor, it pages `users.history.list(startHistoryId=<cursor>, historyTypes=[messageAdded, messageDeleted])`, following `nextPageToken`. It translates `messagesAdded[]` to live `DocRef`s and `messagesDeleted[]` to tombstone `DocRef`s (`deleted=True`), advancing the cursor to the newest reported `historyId`. The durable, resumable point is the historyId, not a list pageToken (which is only intra-listing pagination).
- **Deletions:** `messagesDeleted[]` ("Messages deleted, not Trashed, from the mailbox") becomes a `DocRef(deleted=True)` tombstone so the runtime removes the document.
- **Expired cursor:** an expired `startHistoryId` is signalled by the API with HTTP 404. The connector catches it and downgrades to a full pass (fresh profile historyId plus a full `messages.list`) so the runtime recovers rather than stalls.

The runtime owns retries, backoff, and resumable sync; the connector only describes how to talk to Gmail for one page or one message.

## Known limits

- **Trashing is not a deletion.** Trashing surfaces as `labelsAdded` TRASH, not `messagesDeleted`. This connector treats only permanent removal as a tombstone; a trashed message is not removed.
- **Body is the snippet only.** `fetch` stores Gmail's `snippet` as the message text. Full body extraction (walking `payload.parts`) is not yet implemented.
- **Rate limits** (per-user and per-project quotas) surface as HTTP 429 and are not hidden.

## Conformance

This connector passes the shared conformance suite (`assert_conforms` from `pansoph-connector-sdk`), run hermetically with `uv run pytest -m conformance`.

## References

Gmail API (REST v1), verified 2026-07-05:

- Messages list: https://developers.google.com/gmail/api/reference/rest/v1/users.messages/list
- Messages get: https://developers.google.com/gmail/api/reference/rest/v1/users.messages/get
- History list: https://developers.google.com/gmail/api/reference/rest/v1/users.history/list
- Get profile: https://developers.google.com/gmail/api/reference/rest/v1/users/getProfile
- Incremental and deletion sync: https://developers.google.com/gmail/api/guides/sync
- Access model: https://developers.google.com/workspace/gmail/api/guides
- OAuth scopes: https://developers.google.com/workspace/gmail/api/auth/scopes
- Google Identity OAuth 2.0 (web server): https://developers.google.com/identity/protocols/oauth2/web-server
