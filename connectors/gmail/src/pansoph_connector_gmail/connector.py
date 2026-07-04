"""The gmail connector.

Implemented against the Gmail API (REST v1). Verified against the official docs:

  API docs:    https://developers.google.com/gmail/api/reference/rest/v1/users.messages/list
               https://developers.google.com/gmail/api/reference/rest/v1/users.messages/get
               https://developers.google.com/gmail/api/guides/sync (incremental sync)
               https://developers.google.com/gmail/api/guides/filtering (q search)
  API version: v1
  Base URL:    https://gmail.googleapis.com/gmail/v1 (config base_url override).
  Auth:        OAuth 2.0 bearer access token. The connector declares an OAUTH config
               field (key "token", oauth_provider "google", scope gmail.readonly), so
               the in-app Google OAuth flow (access_type=offline, prompt=consent) mints
               and seals the token into that field; a current access token may also be
               pasted by hand into the same field. Verified against Google Identity
               OAuth 2.0 web server docs on 2026-07-04:
               https://developers.google.com/identity/protocols/oauth2/web-server
               The token is sent as "Authorization: Bearer <token>" on every request.
               The mailbox owner is config "user_email", and the API path uses the
               special userId "me" to mean that authenticated mailbox.
  Pagination:  users.messages.list returns up to maxResults ids per page and a
               nextPageToken. Follow nextPageToken until it is absent.
  Incremental: Gmail exposes two mechanisms. The strongest is users.history.list
               keyed by historyId, which returns the changes since a prior point.
               A lighter alternative is the search query q=newer_than:Nd. The cursor
               this connector returns is a placeholder for that resumable point; the
               runtime owns the durable cursor and resumes from it.
  Rate limit:  per user and per project quotas, surfaced as 429, not hidden.
  ACL model:   a Gmail mailbox is private to its owner. Gmail has no per message
               sharing surface in the API, so access is "the mailbox owner" and
               nobody else. Every message therefore maps to an AccessControl that
               allows exactly the owner (config "user_email"), is not public, and is
               namespaced by the instance (principal_ns = instance_id). This is
               coarse but honest: it never overshares, because the owner is the only
               principal who can see the mailbox.
  Verified on: 2026-06-20.

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
                incremental=False, permission_sync=True, deletions=False, slim_listing=True
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
                    oauth_scopes=("https://www.googleapis.com/auth/gmail.readonly",),
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
        async with self._client() as client:
            page_token: str | None = None
            while True:
                params: dict[str, Any] = {"maxResults": self._max_results}
                if page_token:
                    params["pageToken"] = page_token
                resp = await client.get("/gmail/v1/users/me/messages", params=params)
                resp.raise_for_status()
                payload = resp.json()
                next_token = payload.get("nextPageToken") or ""
                for msg in payload.get("messages", []):
                    yield self._ref(msg["id"]), next_token
                if not next_token:
                    break
                page_token = next_token

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
        # No HTTP needed: a Gmail mailbox is private to its owner, with no per message
        # sharing in the API, so the ACL is fully determined by config.
        return self._acl()

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        return []

    # --- helpers ---
    def _client(self) -> httpx.AsyncClient:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=30.0)

    def _ref(self, message_id: str) -> DocRef:
        return DocRef(source_doc_id=message_id, instance_id=self.instance_id)

    def _acl(self) -> AccessControl:
        # The mailbox owner is the only principal who can see any message. Not public,
        # no groups, no per message sharing. Coarse but never oversharing.
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
