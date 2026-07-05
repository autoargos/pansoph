"""The gmail connector.

Implemented against the Gmail API (REST v1). Verified against the official docs.

  API docs:    https://developers.google.com/gmail/api/reference/rest/v1/users.messages/list
               https://developers.google.com/gmail/api/reference/rest/v1/users.messages/get
               https://developers.google.com/gmail/api/reference/rest/v1/users.history/list
               https://developers.google.com/gmail/api/reference/rest/v1/users/getProfile
               https://developers.google.com/gmail/api/guides/sync (incremental + deletion sync)
               https://developers.google.com/gmail/api/guides/filtering (q search)
               https://developers.google.com/workspace/gmail/api/guides (access model)
               https://developers.google.com/workspace/gmail/api/auth/scopes (scopes)
               (delegation model)
               https://knowledge.workspace.google.com/admin/gmail/let-users-delegate-access-to-a-gmail-account
  API version: v1
  Base URL:    https://gmail.googleapis.com/gmail/v1 (config base_url override).
  Auth:        OAuth 2.0 bearer access token. The connector declares an OAUTH config
               field (key "token", oauth_provider "google", scope gmail.readonly), so
               the in-app Google OAuth flow (access_type=offline, prompt=consent) mints
               and seals the token into that field; a current access token may also be
               pasted by hand into the same field. Verified against Google Identity
               OAuth 2.0 web server docs:
               https://developers.google.com/identity/protocols/oauth2/web-server
               The token is sent as "Authorization: Bearer <token>" on every request.
               The mailbox owner is config "user_email", and the API path uses the
               special userId "me" to mean that authenticated mailbox.
  Pagination:  users.messages.list and users.history.list both return up to maxResults
               items per page plus a nextPageToken. Follow nextPageToken until absent.
  Incremental: Real partial sync uses users.history.list keyed by startHistoryId. The
               durable cursor is a Gmail historyId, sourced from users.getProfile at the
               start of the full pass (the profile's historyId is the mailbox's current
               history record). On a subsequent pass the connector calls
               users.history.list(startHistoryId=<cursor>, historyTypes=messageAdded and
               messageDeleted) and translates:
                 - messagesAdded[]   -> a live DocRef,
                 - messagesDeleted[] -> a tombstone DocRef (deleted=True).
               An expired startHistoryId is signalled by the API with HTTP 404; the
               connector treats that as "cursor too old" and falls back to a full pass
               (a fresh profile historyId + a full messages.list), which the runtime
               then resumes from. historyId, not a messages.list pageToken, is the
               durable resumable point; pageToken is only intra-listing pagination.
  Deletions:  users.history.list surfaces messagesDeleted[] ("Messages deleted, not
               Trashed, from the mailbox"). Each becomes a DocRef(deleted=True) so the
               runtime removes the document. Trashing is NOT a permanent deletion: it
               surfaces as labelsAdded TRASH, and this connector does not treat a trash
               as a deletion (only permanent removal is a tombstone).
  Rate limit:  per user and per project quotas, surfaced as 429, not hidden.
  ACL model:   a Gmail mailbox is private to its owner. For the gmail.readonly scope
               this connector requests, Gmail exposes NO per message sharing surface and
               NO reachable additional principals: access is whole-mailbox and gated by
               the OAuth scope the owner granted. So every message maps to an
               AccessControl that allows exactly the owner (config "user_email"), is not
               public, has no groups and no deny, and is namespaced by the instance
               (principal_ns = instance_id). This is coarse but faithful for this scope:
               it never overshares, because the owner is the only principal the
               credential can reach.
               SCOPE CAVEAT (not a bug at gmail.readonly): the only Gmail sharing surface
               is coarse whole-mailbox delegation (settings.delegates), which is gated
               behind the separate gmail.settings.sharing scope this connector does NOT
               request. If a future instance ever widens the credential to include
               gmail.settings.sharing, the ACL mapping MUST also enumerate
               users.settings.delegates as allow_users, or it would under-share (over-deny)
               for that scope. Until then, owner-only is the faithful mapping.
  Verified on: 2026-07-05.

Credential scope note: every configured instance carries its own OAuth credential
for one mailbox, and its own ACL namespace (principal_ns = instance_id). Two
instances over two mailboxes never collide. See pansoph CLAUDE.md section 6.

The runtime owns retries, backoff, and resumable sync, so a method only describes
how to talk to Gmail for one page or one message.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
from pansoph_connector_sdk import (
    ChangeEvent,
    ConfigField,
    ConnectorMeta,
    ConnectorSpec,
    FieldKind,
    ValidationResult,
)
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc

# The OAuth scope this connector requests. Whole-mailbox read only, no delegation.
_SCOPE_READONLY = "https://www.googleapis.com/auth/gmail.readonly"


class GmailConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        self.instance_id = instance_id
        cfg = config or {}
        self._token: str = cfg.get("token", "")
        # The mailbox owner. Used as the single ACL principal. The API itself uses the
        # special userId "me" to address the authenticated mailbox.
        self._user_email: str = cfg.get("user_email", "")
        self._base_url: str = cfg.get("base_url", "https://gmail.googleapis.com")
        self._max_results: int = int(cfg.get("max_results", 100))
        self.meta = ConnectorMeta(
            name="gmail",
            auth=AuthScheme.oauth2,
            capabilities=Capabilities(
                incremental=True, permission_sync=True, deletions=True, slim_listing=True
            ),
        )

    @classmethod
    def spec(cls) -> ConnectorSpec:
        return ConnectorSpec(
            name="gmail",
            title="Gmail",
            description=(
                "Ingest a Gmail mailbox with an OAuth 2.0 access token. The mailbox is "
                "private to its owner, so every message is readable only by that owner."
            ),
            fields=(
                ConfigField(
                    key="token",
                    label="Gmail (OAuth)",
                    kind=FieldKind.OAUTH,
                    oauth_provider="google",
                    oauth_scopes=(_SCOPE_READONLY,),
                    help=(
                        "Connect Gmail with Google OAuth 2.0 (read only, "
                        "gmail.readonly). The in-app flow seals the access token (and a "
                        "refresh token) into this field. You can still paste a current "
                        "bearer access token by hand; it is sealed in the secret store "
                        "and sent as Authorization: Bearer on every request."
                    ),
                ),
                ConfigField(
                    key="user_email",
                    label="Mailbox owner email",
                    kind=FieldKind.STRING,
                    placeholder="alice@example.com",
                    help=(
                        "The mailbox owner. Used as the single ACL principal, since a "
                        "Gmail mailbox is private to its owner with no per message sharing."
                    ),
                ),
                ConfigField(
                    key="base_url",
                    label="API base URL",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="https://gmail.googleapis.com",
                    help="Override only for a proxy or self-managed gateway.",
                ),
                ConfigField(
                    key="max_results",
                    label="Page size",
                    kind=FieldKind.INT,
                    required=False,
                    placeholder="100",
                    help="Messages requested per list page (Gmail maxResults).",
                ),
            ),
        )

    # --- contract ---
    def validate(self, config: dict[str, Any]) -> ValidationResult:
        missing = [k for k in ("token", "user_email") if not config.get(k)]
        if missing:
            return ValidationResult(ok=False, error=f"missing config: {', '.join(missing)}")
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        """Yield (DocRef, cursor) pairs.

        With no cursor this is a full pass: it captures the mailbox's current historyId
        from users.getProfile as the durable cursor, then pages users.messages.list and
        yields a live DocRef for each message carrying that historyId. With a cursor it
        is a partial pass: it pages users.history.list(startHistoryId=cursor) and yields
        a live DocRef for every messagesAdded entry and a tombstone DocRef (deleted=True)
        for every messagesDeleted entry, each carrying the newest historyId as the cursor.
        An expired cursor (HTTP 404) is caught and downgraded to a full pass so the
        runtime can recover rather than stall.
        """
        async with self._client() as client:
            if cursor:
                try:
                    async for ref, next_cursor in self._history_changes(client, cursor):
                        yield ref, next_cursor
                    return
                except httpx.HTTPStatusError as exc:
                    # An expired startHistoryId is outside the available history range;
                    # the API signals it with 404. Fall through to a full resync.
                    if exc.response.status_code != 404:
                        raise
            async for ref, next_cursor in self._full_changes(client):
                yield ref, next_cursor

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        async with self._client() as client:
            resp = await client.get(
                f"/gmail/v1/users/me/messages/{ref.source_doc_id}",
                params={"format": "full"},
            )
            resp.raise_for_status()
            payload = resp.json()
            subject = self._header(payload, "Subject")
            # response["snippet"] is Gmail's own short plain text excerpt of the body.
            # It is the first cut of the text; a richer parse would walk payload.parts.
            snippet = payload.get("snippet", "")
            raw = RawDoc(
                source_doc_id=ref.source_doc_id,
                instance_id=self.instance_id,
                mime="text/plain",
                text=snippet,
                title=subject,
            )
            return raw, self._acl()

    async def list_acls(self, ref: DocRef) -> AccessControl:
        # No HTTP needed. For the gmail.readonly scope this connector requests, a Gmail
        # mailbox is private to its owner with no per message sharing and no reachable
        # delegates, so the ACL is fully determined by config. See the ACL model note in
        # the module header, including the gmail.settings.sharing scope caveat.
        return self._acl()

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        return []

    # --- change feeds ---
    async def _full_changes(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[DocRef, str]]:
        # The durable cursor for a full pass is the mailbox's current historyId, captured
        # up front so every yielded ref carries a resumable point even on a single page.
        cursor = await self._current_history_id(client)
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"maxResults": self._max_results}
            if page_token:
                params["pageToken"] = page_token
            resp = await client.get("/gmail/v1/users/me/messages", params=params)
            resp.raise_for_status()
            payload = resp.json()
            for msg in payload.get("messages", []):
                yield self._ref(msg["id"]), cursor
            page_token = payload.get("nextPageToken") or ""
            if not page_token:
                break

    async def _history_changes(
        self, client: httpx.AsyncClient, start_history_id: str
    ) -> AsyncIterator[tuple[DocRef, str]]:
        # Real partial sync: translate messagesAdded to live refs and messagesDeleted to
        # tombstones. The cursor advances to the newest historyId the API reports, which
        # is the response-level historyId when present, else the start point.
        page_token: str | None = None
        cursor = start_history_id
        while True:
            params: dict[str, Any] = {
                "startHistoryId": start_history_id,
                "maxResults": self._max_results,
                "historyTypes": ["messageAdded", "messageDeleted"],
            }
            if page_token:
                params["pageToken"] = page_token
            resp = await client.get("/gmail/v1/users/me/history", params=params)
            resp.raise_for_status()
            payload = resp.json()
            cursor = str(payload.get("historyId") or cursor)
            for record in payload.get("history", []):
                for added in record.get("messagesAdded", []):
                    message = added.get("message", {})
                    if message.get("id"):
                        yield self._ref(message["id"]), cursor
                for removed in record.get("messagesDeleted", []):
                    message = removed.get("message", {})
                    if message.get("id"):
                        yield self._tombstone(message["id"]), cursor
            page_token = payload.get("nextPageToken") or ""
            if not page_token:
                break

    async def _current_history_id(self, client: httpx.AsyncClient) -> str:
        # users.getProfile.historyId is the mailbox's current history record, the point a
        # later partial sync resumes from.
        resp = await client.get("/gmail/v1/users/me/profile")
        resp.raise_for_status()
        return str(resp.json().get("historyId") or "")

    # --- helpers ---
    def _client(self) -> httpx.AsyncClient:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=30.0)

    def _ref(self, message_id: str) -> DocRef:
        return DocRef(source_doc_id=message_id, instance_id=self.instance_id)

    def _tombstone(self, message_id: str) -> DocRef:
        # A permanently deleted message. The runtime removes it; the ref still identifies
        # the document by source_doc_id and instance_id.
        return DocRef(source_doc_id=message_id, instance_id=self.instance_id, deleted=True)

    def _acl(self) -> AccessControl:
        # The mailbox owner is the only principal who can see any message under the
        # gmail.readonly scope. Not public, no groups, no deny, no per message sharing.
        # Coarse but never oversharing. See the module header for the scope caveat.
        return AccessControl(
            allow_users=[self._user_email] if self._user_email else [],
            public=False,
            principal_ns=self.instance_id,
        )

    @staticmethod
    def _header(payload: dict[str, Any], name: str) -> str | None:
        for header in payload.get("payload", {}).get("headers", []):
            if header.get("name") == name:
                return header.get("value")
        return None
