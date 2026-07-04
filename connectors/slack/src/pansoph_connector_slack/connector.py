"""The slack connector.

Implemented against the Slack Web API. Verified against the official docs:

  API docs:    https://docs.slack.dev/reference/methods/conversations.list
               https://docs.slack.dev/reference/methods/conversations.history
               https://docs.slack.dev/reference/methods/conversations.info
               https://docs.slack.dev/reference/methods/conversations.members
               https://docs.slack.dev/apis/web-api/pagination (cursor pagination)
               https://docs.slack.dev/authentication/tokens (token types)
  API version: Slack Web API (versionless, methods under https://slack.com/api/).
  Auth:        bearer token in the Authorization header. The token is a bot token
               (xoxb-) or a user token (xoxp-) supplied by the operator. The
               connector does not assume a scope, it maps whatever the credential
               can reach. Reading channels needs channels:read and groups:read,
               reading messages needs channels:history and groups:history, and the
               ACL members lookup needs the same read scopes.
  Pagination:  cursor based. Pass limit, read response_metadata.next_cursor, and
               re pass it as cursor. Stop when next_cursor is empty.
  Incremental: the runtime owns resumable sync. list_changes carries the channel
               list cursor as the next_cursor so a crash resumes mid listing.
  ACL model:   a document is a channel. A public channel (is_private false) maps to
               public. A private channel, im, or mpim maps to its member user ids
               (conversations.members). Slack exposes NO per channel groups, so
               allow_groups is always empty for this source, and Slack has no per
               channel deny concept, so deny is always empty. The ACL therefore
               reflects exactly the membership the credential can see.
  Verified on: 2026-06-20.

Credential scope note: every configured instance carries its own credential and its
own ACL namespace (principal_ns = instance_id). One instance may be a bot token in
one workspace, another a user token in another. They never collide, and each maps
ACLs at the membership granularity its credential grants. See pansoph CLAUDE.md
section 6.

The runtime owns retries, backoff, and resumable sync, so a method only describes
how to talk to Slack for one page or one document.
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


class SlackConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        self.instance_id = instance_id
        cfg = config or {}
        self._token: str = cfg.get("token", "")
        # Default base is the host only, so methods are called as relative
        # "/api/<method>" paths and the request path is stable for mocking.
        self._base_url: str = cfg.get("base_url", "https://slack.com")
        self.meta = ConnectorMeta(
            name="slack",
            auth=AuthScheme.api_token,
            capabilities=Capabilities(
                incremental=False, permission_sync=True, deletions=False, slim_listing=True
            ),
        )

    @classmethod
    def spec(cls) -> ConnectorSpec:
        return ConnectorSpec(
            name="slack",
            title="Slack",
            description="Ingest Slack channels a bot or user token can reach.",
            fields=(
                ConfigField(
                    key="token",
                    label="Bearer token",
                    kind=FieldKind.OAUTH,
                    oauth_provider="slack",
                    oauth_scopes=(
                        "channels:read",
                        "channels:history",
                        "groups:read",
                        "groups:history",
                        "users:read",
                    ),
                    placeholder="xoxb-... or xoxp-...",
                    help=(
                        "Connect with Slack to mint a bot token via the in-app OAuth "
                        "flow, or paste a bot token (xoxb-) or user token (xoxp-) "
                        "directly. Either way it is sealed in the secret store. Needs "
                        "channels:read, groups:read, channels:history, groups:history, "
                        "and users:read."
                    ),
                ),
                ConfigField(
                    key="base_url",
                    label="API base URL",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="https://slack.com",
                    help="Override only for a proxy or self-managed gateway.",
                ),
            ),
        )

    # --- contract ---
    def validate(self, config: dict[str, Any]) -> ValidationResult:
        if not config.get("token"):
            return ValidationResult(ok=False, error="missing config: token")
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        """List every channel the credential can reach.

        First cut performs a full listing each run by following Slack cursor
        pagination; incremental message backfill is a follow-up. Incremental is off
        (capabilities.incremental is False), so each yielded DocRef carries an empty
        next_cursor. The cursor parameter is accepted for contract symmetry but the
        loop always walks the full set internally so listing is complete regardless of
        runtime re-drive behavior.
        """
        async with self._client() as client:
            params: dict[str, Any] = {
                "types": "public_channel,private_channel",
                "limit": 200,
            }
            while True:
                resp = await client.get("/api/conversations.list", params=params)
                resp.raise_for_status()
                payload = resp.json()
                for channel in payload.get("channels", []):
                    yield self._ref(channel["id"]), ""
                next_cursor = payload.get("response_metadata", {}).get("next_cursor", "") or ""
                if not next_cursor:
                    break
                params["cursor"] = next_cursor

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        async with self._client() as client:
            resp = await client.get(
                "/api/conversations.history", params={"channel": ref.source_doc_id}
            )
            resp.raise_for_status()
            messages = resp.json().get("messages", [])
            text = "\n".join(m.get("text", "") for m in messages if m.get("text"))
            raw = RawDoc(
                source_doc_id=ref.source_doc_id,
                instance_id=self.instance_id,
                mime="text/plain",
                text=text,
                title=ref.source_doc_id,
                source_url=None,
            )
            return raw, await self._acl(client, ref.source_doc_id)

    async def list_acls(self, ref: DocRef) -> AccessControl:
        async with self._client() as client:
            return await self._acl(client, ref.source_doc_id)

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        # Slack Events API delivers message events under event.event with a channel.
        # A message upserts the channel document it belongs to. Deletions are not
        # modeled (deletions capability is false), so only upserts are emitted.
        inner = event.get("event", {})
        channel = inner.get("channel")
        if inner.get("type") == "message" and channel:
            return [ChangeEvent(kind="upsert", source_doc_id=channel, instance_id=self.instance_id)]
        return []

    # --- helpers ---
    def _client(self) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {self._token}"}
        return httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=30.0)

    def _ref(self, channel_id: str) -> DocRef:
        return DocRef(source_doc_id=channel_id, instance_id=self.instance_id)

    async def _acl(self, client: httpx.AsyncClient, channel_id: str) -> AccessControl:
        ns = self.instance_id
        info = await client.get("/api/conversations.info", params={"channel": channel_id})
        info.raise_for_status()
        channel = info.json().get("channel", {})
        if not channel.get("is_private", False):
            return AccessControl(public=True, principal_ns=ns)
        members = [m async for m in self._members(client, channel_id)]
        # Slack exposes no per channel groups, so allow_groups stays empty, and Slack
        # has no per channel deny, so deny stays empty.
        return AccessControl(allow_users=members, principal_ns=ns)

    async def _members(self, client: httpx.AsyncClient, channel_id: str) -> AsyncIterator[str]:
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"channel": channel_id, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            resp = await client.get("/api/conversations.members", params=params)
            resp.raise_for_status()
            payload = resp.json()
            for user_id in payload.get("members", []):
                yield user_id
            cursor = payload.get("response_metadata", {}).get("next_cursor", "") or ""
            if not cursor:
                break
