"""The Microsoft Teams connector.

Implemented against the Microsoft Graph REST API (v1.0). Verified against the
official docs:

  API docs:    https://learn.microsoft.com/graph/api/channel-list?view=graph-rest-1.0
                 (list channels)
               https://learn.microsoft.com/graph/api/resources/channel?view=graph-rest-1.0
                 (channel resource, the membershipType property: standard, private,
                  shared)
               https://learn.microsoft.com/graph/api/channel-list-messages?view=graph-rest-1.0
                 (list channel messages)
               https://learn.microsoft.com/graph/api/channel-list-members?view=graph-rest-1.0
                 (list channel members: DIRECT members only, for every channel type)
               https://learn.microsoft.com/graph/api/channel-list-allmembers?view=graph-rest-1.0
                 (list allMembers: direct AND indirect members, all channel types)
               https://learn.microsoft.com/graph/api/team-list-members?view=graph-rest-1.0
                 (list team members: the roster a standard channel inherits)
               https://learn.microsoft.com/graph/api/resources/conversationmember?view=graph-rest-1.0
                 (conversationMember resource, the userId property)
  API version: Graph v1.0 (paths are prefixed /v1.0/...).
  Auth:        app-only OAuth2 bearer token, operator provided. The configured
               token is sent as `Authorization: Bearer <token>`. The connector
               does not mint or refresh the token; the operator supplies an access
               token already scoped for the credential's tenant and app
               registration (for example ChannelMember.Read.Group or
               ChannelMember.Read.All for channel members, TeamMember.Read.All for
               the team roster, and the channel message read permissions). Because
               access is RBAC controlled, the ACL reflects the credential's actual
               reach, not an assumption.
  Pagination:  Graph returns at most a page of items plus an `@odata.nextLink`
               absolute URL. Follow `@odata.nextLink` until it is absent.
  Incremental: not implemented in this first cut. Capabilities.incremental is
               False, so the runtime performs full listings. `next_cursor` is the
               empty string, matching the other non-incremental connectors. (Graph
               offers change notifications and the chatMessage delta query that a
               later revision can adopt to set incremental=True honestly, exactly as
               the SharePoint connector does with the driveItem delta feed.)
  Deletions:   not implemented in this first cut. Capabilities.deletions is False.
               list_changes never yields a tombstone (a DocRef with deleted=True),
               so the flag is honest: the conformance suite requires a
               deletions-capable connector to actually surface a tombstone, so the
               flag stays False until the chatMessage delta feed (which reports a
               removed message through the deletedDateTime property) is wired up.
  Rate limit:  Graph throttles per app and per tenant and returns 429 with a
               Retry-After header; the runtime owns retries and backoff, so a
               method only describes one page or one document.
  ACL model:   a document is a channel, and the channel ACL is the set of users who
               can actually see that channel, which depends on the channel's
               membershipType (see the channel resource docs). Getting this wrong is
               a security bug, not a cosmetic one, because the channel/members
               endpoint returns only DIRECT members for every channel type:

                 - standard: a standard channel is visible to the whole parent team
                   and its membership is INHERITED from the team, not stored on the
                   channel. The channel/members endpoint does not return the team
                   roster for a standard channel, so mapping it would under-share
                   (omit team members who can read the channel). The faithful source
                   is the team members endpoint, /teams/{team}/members.

                 - private and shared: access is the channel's own membership, which
                   for a shared channel includes INDIRECT members (users who reach
                   the channel through a team it is shared with). The allMembers
                   endpoint returns both direct and indirect members; the plain
                   members endpoint would omit indirect members and under-share. So
                   these map from /teams/{team}/channels/{id}/allMembers.

               Each conversationMember exposes a `userId` (the Entra object id) which
               becomes an allow_user. Members without a userId (for example an
               anonymous guest conversationMember) are skipped, since they carry no
               principal to map. No allow_groups and no deny are emitted (Graph
               surfaces channel access at the member level here). See the caveat.
  Verified on: 2026-07-05 (channel resource, list members, list allMembers, and list
               team members re-verified against the v1.0 reference on this date; the
               earlier 2026-06-20 cut mapped every channel type to channel/members,
               which under-shares standard and shared channels, and is corrected
               here).

ACL caveat (security-relevant, read before changing the mapping): membership is
mapped to allow_users only, at the userId granularity Graph exposes. Group-backed
access is not expanded to allow_groups in this revision; the userId lists already
name the concrete principals, so the authz layer has the truth it needs. For a
shared channel, allMembers may list the same user more than once (once directly and
once per sharing team), so the userId list is deduplicated. Deny semantics do not
exist in this Teams access model, so deny stays empty.

Credential scope note: every configured instance carries its own credential and
its own ACL namespace (principal_ns = instance_id). The members' userId values are
Entra object ids within that credential's tenant, so two instances never collide.
See pansoph CLAUDE.md section 6.

The runtime owns retries, backoff, and resumable sync, so a method only describes
how to talk to Graph for one page or one document.
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


class TeamsConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        self.instance_id = instance_id
        cfg = config or {}
        self._token: str = cfg.get("token", "")
        self._team_id: str = cfg.get("team_id", "")
        self._base_url: str = cfg.get("base_url", "https://graph.microsoft.com")
        self.meta = ConnectorMeta(
            name="teams",
            auth=AuthScheme.oauth2,
            capabilities=Capabilities(
                incremental=False, permission_sync=True, deletions=False, slim_listing=True
            ),
        )

    @classmethod
    def spec(cls) -> ConnectorSpec:
        return ConnectorSpec(
            name="teams",
            title="Microsoft Teams",
            description=(
                "Ingest Microsoft Teams channel messages via the Microsoft Graph API. "
                "Paste an app-only OAuth2 access token scoped for the team's tenant."
            ),
            fields=(
                ConfigField(
                    key="token",
                    label="Access token",
                    kind=FieldKind.SECRET,
                    placeholder="Bearer access token (eyJ0eXAiOi...)",
                    help=(
                        "Microsoft Graph app-only OAuth2 access token, sent as "
                        "Authorization: Bearer <token>. The connector does not mint or "
                        "refresh it. Sealed in the secret store."
                    ),
                ),
                ConfigField(
                    key="team_id",
                    label="Team id",
                    kind=FieldKind.STRING,
                    placeholder="19:abc123...@thread.tacv2 or the team GUID",
                    help="The Entra group id of the team whose channels to ingest.",
                ),
                ConfigField(
                    key="base_url",
                    label="API base URL",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="https://graph.microsoft.com",
                    help="Override only for a national cloud or a proxy gateway.",
                ),
            ),
        )

    # --- contract ---
    def validate(self, config: dict[str, Any]) -> ValidationResult:
        missing = [k for k in ("token", "team_id") if not config.get(k)]
        if missing:
            return ValidationResult(ok=False, error=f"missing config: {', '.join(missing)}")
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        # A document is a channel. Incremental sync is not implemented, so every run
        # lists all channels and the cursor stays empty.
        async with self._client() as client:
            async for channel in self._paginate(client, f"/v1.0/teams/{self._team_id}/channels"):
                channel_id = channel.get("id")
                if channel_id:
                    yield self._ref(channel_id), ""

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        async with self._client() as client:
            parts: list[str] = []
            async for message in self._paginate(
                client,
                f"/v1.0/teams/{self._team_id}/channels/{ref.source_doc_id}/messages",
            ):
                body = message.get("body") or {}
                content = body.get("content")
                if content:
                    parts.append(content)
            raw = RawDoc(
                source_doc_id=ref.source_doc_id,
                instance_id=self.instance_id,
                mime="text/html",
                text="\n".join(parts),
                title=ref.source_doc_id,
            )
            return raw, await self._acl(client, ref.source_doc_id)

    async def list_acls(self, ref: DocRef) -> AccessControl:
        async with self._client() as client:
            return await self._acl(client, ref.source_doc_id)

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        # Graph change notifications are not wired up in this first cut.
        return []

    # --- helpers ---
    def _client(self) -> httpx.AsyncClient:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=30.0)

    def _ref(self, channel_id: str) -> DocRef:
        return DocRef(source_doc_id=channel_id, instance_id=self.instance_id)

    async def _acl(self, client: httpx.AsyncClient, channel_id: str) -> AccessControl:
        # The faithful ACL depends on the channel's membershipType, because the
        # channel/members endpoint returns only DIRECT members for every type. A
        # standard channel inherits its membership from the parent team, and a shared
        # channel has indirect members; both are missed by channel/members. See the
        # module ACL model note.
        membership_type = await self._membership_type(client, channel_id)
        if membership_type == "standard":
            # Visible to the whole team; the roster lives on the team, not the channel.
            path = f"/v1.0/teams/{self._team_id}/members"
        else:
            # private or shared (and any future type): allMembers covers direct plus
            # indirect members, so it never under-shares relative to channel/members.
            path = f"/v1.0/teams/{self._team_id}/channels/{channel_id}/allMembers"

        users: list[str] = []
        async for member in self._paginate(client, path):
            user_id = member.get("userId")
            if user_id:
                users.append(user_id)
        return AccessControl(allow_users=_dedupe(users), principal_ns=self.instance_id)

    async def _membership_type(self, client: httpx.AsyncClient, channel_id: str) -> str:
        # Read the channel's membershipType to choose the faithful membership source.
        # Absent or unrecognized values fall back to the allMembers path, which is the
        # safe superset (it never omits a member the plain members endpoint would).
        resp = await client.get(f"/v1.0/teams/{self._team_id}/channels/{channel_id}")
        resp.raise_for_status()
        return resp.json().get("membershipType") or ""

    async def _paginate(
        self, client: httpx.AsyncClient, path: str
    ) -> AsyncIterator[dict[str, Any]]:
        # Graph returns a "value" array per page and an absolute "@odata.nextLink"
        # URL for the next page. Follow nextLink until it is absent.
        url: str | None = path
        while url:
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.json()
            for item in payload.get("value", []):
                yield item
            url = payload.get("@odata.nextLink")


def _dedupe(values: list[str]) -> list[str]:
    # A shared channel's allMembers can list the same userId more than once (once
    # directly and once per sharing team). Preserve first-seen order.
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
