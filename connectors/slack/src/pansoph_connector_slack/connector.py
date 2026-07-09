"""The slack connector.

Implemented against the Slack Web API. Verified against the official docs:

  API docs:    https://docs.slack.dev/apis/web-api/
               https://docs.slack.dev/apis/web-api/rate-limits/
               https://docs.slack.dev/apis/web-api/using-the-conversations-api/
               https://docs.slack.dev/reference/methods/conversations.list/
               https://docs.slack.dev/reference/methods/conversations.history/
               https://docs.slack.dev/reference/methods/conversations.info/
               https://docs.slack.dev/reference/methods/conversations.members/
               https://docs.slack.dev/apis/web-api/pagination (cursor pagination)
               https://docs.slack.dev/authentication/tokens (token types)
               Guest access (load bearing for the public-channel ACL, see below):
               https://slack.com/help/articles/202518103-Guest-roles-in-Slack
               Deletion signals (not consumed, see below):
               https://api.slack.com/events/channel_deleted
               https://api.slack.com/events/group_deleted
               https://api.slack.com/events/message/message_deleted
  API version: Slack Web API (versionless, methods under https://slack.com/api/).
  Verified on: 2026-07-05.

  Response envelope (load bearing for ACL correctness):
    Every Slack Web API response is a JSON object with a top level boolean "ok".
    On failure Slack returns HTTP 200 with {"ok": false, "error": "<code>"} (docs:
    docs.slack.dev/apis/web-api/). So an HTTP status check alone (raise_for_status)
    does NOT catch an application error: channel_not_found, missing_scope,
    not_in_channel, invalid_auth, token_revoked all arrive as HTTP 200 with
    ok=false. Every call therefore goes through _ok(), which raises SlackApiError on
    ok=false. This is a security invariant: an unreadable or unresolvable channel
    must NEVER fall through to a public or empty ACL (see _acl). Fail closed.

    Rate limits arrive as HTTP 429 with a Retry-After header and body
    {"ok": false, "error": "ratelimited"} (docs.slack.dev/apis/web-api/rate-limits/).
    The runtime owns retries and backoff, so the connector surfaces 429 by raising
    (raise_for_status on 429), and it exposes the Retry-After seconds on the raised
    SlackRateLimited error so the runtime can honor it. The connector does not sleep
    or retry itself.

  Discovery:   conversations.list returns channels[] with id, name, is_private,
               is_archived, and response_metadata.next_cursor. Requested types are
               public_channel,private_channel; limit is 200 (the doc-recommended
               page size). list_resources exposes each reachable channel as a
               Resource of kind "channel", cursor paginated by the same next_cursor.
  Auth:        bearer token in the Authorization header. The token is a bot token
               (xoxb-) or a user token (xoxp-) supplied by the operator. The
               connector does not assume a scope, it maps whatever the credential
               can reach. Reading channels needs channels:read and groups:read,
               reading messages needs channels:history and groups:history, and the
               ACL members lookup needs the same read scopes.
  Pagination:  cursor based. Pass limit, read response_metadata.next_cursor, and
               re pass it as cursor. Stop when next_cursor is empty.
  ACL model:   a document is a channel. A public channel (is_private false) is NOT
               world readable in Slack, so it must NOT map to public. Slack single
               channel and multi channel GUESTS must be explicitly added to a channel
               and cannot read arbitrary public channels (guest-roles help article,
               verified 2026-07-05), and pansoph's public grant resolves to the tenant
               everyone group, which can include principals with no Slack membership at
               all. Mapping a public channel to public=True would therefore over-share
               to guests and to non-Slack tenant users, an AND (readable to full
               members only) that pansoph's UNION-only AccessControl cannot express.
               We fail closed to the narrower side: a public channel maps to the
               namespaced membership group _WORKSPACE_MEMBER_GROUP ("slack:member"),
               the set of FULL workspace members. Until the identity layer materializes
               that group's membership it grants no one, which under-shares (a UX bug),
               never over-shares (a security bug). A private channel, im, or mpim maps
               to its member user ids (conversations.members). Slack exposes NO per
               channel usergroups, so allow_groups otherwise carries only that single
               membership group, and Slack has no per channel deny concept, so deny is
               always empty. If Slack reports ok=false for either the info or the
               members lookup (a channel the credential cannot read, or a private
               channel the bot has not been invited to, which returns not_in_channel
               per the conversations.members reference), _acl RAISES rather than
               producing a public or empty grant. A bot token cannot self join a
               private channel and must be invited (using-the-conversations-api), so
               not_in_channel is a real, expected membership boundary and must fail
               closed, never publish. is_private is defaulted to True (private) when
               absent on an ok=true conversations.info, so the one code path that could
               emit a broad grant fails closed, not open.

  Capabilities (honest as declared):
    incremental=False.  list_changes walks the full conversations.list every run.
      Slack conversations.history does support oldest/latest windowing plus a
      cursor, but there is no workspace level change feed that says "these channels
      changed", so a since-token backfill is a follow up. incremental is declared
      False to match the implementation, no false True. The cursor yielded by
      list_changes is a page level list token (non-empty, required by the shared
      conformance contract), not a cross-run resume high-water mark.
    deletions=False.  Slack DOES expose real deletion signals (channel_deleted,
      group_deleted, and the message_deleted message subtype). The connector does
      not yet consume them: list_changes never yields a tombstone and on_webhook
      emits only upserts. deletions is declared False to match, no false True. To
      turn it on later, wire channel_deleted/group_deleted into on_webhook as
      ChangeEvent(kind="delete") and flip the flag, gated by check_deletions.
    permission_sync=True.  Backed by a real list_acls that maps source membership.

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
from pansoph_types import (
    AccessControl,
    AuthScheme,
    Capabilities,
    DocRef,
    RawDoc,
    Resource,
    ResourcePage,
)

# Sentinel list token yielded alongside a DocRef when the connector has no Slack
# page cursor for that ref (the first and only page, or the final page whose
# next_cursor is empty). The shared conformance contract requires every yielded
# cursor to be a non-empty string, and since incremental sync is off this token is
# never used as a cross-run resume point, it only satisfies the type contract.
_LIST_TOKEN: str = "slack:list"  # noqa: S105  not a secret, a page level list token

# Stable namespaced id for the group of FULL Slack workspace members. A public
# channel is readable by full members but NOT by single/multi-channel guests (who
# must be explicitly added to a channel, guest-roles help article verified
# 2026-07-05), so a public channel maps to allow_groups=[_WORKSPACE_MEMBER_GROUP]
# instead of public=True. This is the fail-closed side of an AND (readable to full
# members only) that pansoph's UNION-only AccessControl cannot express: the pansoph
# tenant-everyone group a public grant resolves to can include principals with no
# Slack membership at all, so public=True would over-share. The identity layer MUST
# materialize membership of this group (the workspace's full members, e.g. via
# users.list filtered to non-guest accounts) for the grant to reach anyone. Until it
# does, an unmaterialized group grants no one, which under-shares (safe) rather than
# over-shares (a leak). The "slack:" prefix namespaces it to this source so it cannot
# collide with an imported source group name.
_WORKSPACE_MEMBER_GROUP: str = "slack:member"


class SlackApiError(RuntimeError):
    """A Slack Web API method returned ok=false.

    Slack returns HTTP 200 with {"ok": false, "error": "<code>"} for application
    errors, so this is raised after parsing the body, not from the HTTP status. It
    carries the machine readable Slack error code so the runtime can classify it
    (for example channel_not_found, missing_scope, not_in_channel, invalid_auth,
    token_revoked). Raising here is the fail-closed guarantee: an unreadable channel
    never degrades into a public or empty ACL.
    """

    def __init__(self, method: str, code: str) -> None:
        self.method = method
        self.code = code
        super().__init__(f"slack {method} returned ok=false: {code}")


class SlackRateLimited(RuntimeError):
    """Slack returned HTTP 429. Carries the Retry-After seconds for the runtime.

    The runtime owns backoff, so the connector surfaces the header value rather than
    sleeping. retry_after is None when Slack omitted or sent an unparseable header.
    """

    def __init__(self, method: str, retry_after: int | None) -> None:
        self.method = method
        self.retry_after = retry_after
        super().__init__(f"slack {method} rate limited, retry after {retry_after}s")


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
                incremental=False,
                permission_sync=True,
                deletions=False,
                slim_listing=True,
                resource_discovery=True,
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
                    key="channel_ids",
                    label="Channels",
                    kind=FieldKind.LIST,
                    required=False,
                    resource_kind="channel",
                    placeholder="C0123ABCD, C0456EFGH",
                    help=(
                        "Optional. Restrict ingestion to specific channels chosen from "
                        "the resource picker (which lists the channels this token can "
                        "reach via list_resources). Leave empty to ingest every "
                        "reachable channel. Falls back to a comma-separated list of "
                        "channel ids when discovery is unavailable."
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

        Performs a full listing each run by following Slack cursor pagination.
        Incremental is off (capabilities.incremental is False), so the cursor
        parameter is accepted for contract symmetry but the loop always walks the
        full set internally. Each yielded DocRef carries a non-empty page level list
        token as required by the shared conformance contract; it is the Slack cursor
        that reached the following page, or the _LIST_TOKEN sentinel for the first
        page and the final page (whose next_cursor is empty). Because incremental is
        off, this token is never persisted as a cross-run resume point.
        """
        async with self._client() as client:
            params: dict[str, Any] = {
                "types": "public_channel,private_channel",
                "limit": 200,
            }
            while True:
                resp = await client.get("/api/conversations.list", params=params)
                payload = self._ok(resp, "conversations.list")
                meta = payload.get("response_metadata", {}) or {}
                next_cursor = meta.get("next_cursor", "") or ""
                # A DocRef on this page is tagged with the token that reaches the
                # NEXT page (or the sentinel when this is the last page), so every
                # yielded cursor is non-empty.
                yield_token = next_cursor or _LIST_TOKEN
                for channel in payload.get("channels", []):
                    # Only ingest a channel the credential is actually a member of.
                    # conversations.list returns is_member for the calling token; a bot
                    # cannot read the history of a channel it was never invited to (fetch
                    # would get not_in_channel and fail closed), so listing it would only
                    # abort the sync on a channel that is correctly inaccessible. Skipping
                    # here ingests exactly what the credential can read. A private channel
                    # the token is in has is_member true.
                    if not channel.get("is_member", False):
                        continue
                    yield self._ref(channel["id"]), yield_token
                if not next_cursor:
                    break
                params["cursor"] = next_cursor

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        async with self._client() as client:
            text = await self._history_text(client, ref.source_doc_id)
            raw = RawDoc(
                source_doc_id=ref.source_doc_id,
                instance_id=self.instance_id,
                mime="text/plain",
                text=text,
                title=ref.source_doc_id,
                source_url=None,
            )
            # ACL and content are fetched together. If either the history call or the
            # ACL call reports ok=false, the exception propagates and the runtime
            # never persists a document with a leaked or empty ACL.
            return raw, await self._acl(client, ref.source_doc_id)

    async def list_acls(self, ref: DocRef) -> AccessControl:
        async with self._client() as client:
            return await self._acl(client, ref.source_doc_id)

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        # Slack Events API delivers message events under event.event with a channel.
        # A message upserts the channel document it belongs to. Deletions are not
        # modeled (deletions capability is false), so only upserts are emitted. Slack
        # does expose channel_deleted/group_deleted/message_deleted; consuming them
        # is a follow up gated by check_deletions, see the module header.
        inner = event.get("event", {})
        channel = inner.get("channel")
        if inner.get("type") == "message" and channel:
            return [ChangeEvent(kind="upsert", source_doc_id=channel, instance_id=self.instance_id)]
        return []

    async def list_resources(
        self,
        kind: str | None = None,
        parent_id: str | None = None,
        cursor: str | None = None,
    ) -> ResourcePage:
        """List the channels this credential can reach, as pickable Resources.

        Slack is a flat workspace of channels for discovery purposes, so there is a
        single kind, "channel", and no parent hierarchy: every channel is top level
        and parent_id is always None. A None kind means "the top level", which for
        Slack is the channels themselves, so both None and "channel" list channels;
        any other kind lists nothing. parent_id is accepted for contract symmetry and
        must be None (there is no level below a channel to drill into).

        One Slack conversations.list page maps to one ResourcePage: the passed cursor
        is Slack's own next_cursor (None means the first page), and the returned
        next_cursor is response_metadata.next_cursor, or None when Slack returns an
        empty cursor (the listing is exhausted). Only channels the token can see are
        returned, so discovery is exactly the reach the credential already grants and
        never a scope escalation. The Resource id is the opaque channel id the
        connector needs (it flows straight into a DocRef), and the label is the human
        channel name, prefixed to mark private channels for the picker.
        """
        if kind not in (None, "channel"):
            return ResourcePage(items=[], next_cursor=None)
        if parent_id is not None:
            return ResourcePage(items=[], next_cursor=None)

        params: dict[str, Any] = {
            "types": "public_channel,private_channel",
            "limit": 200,
            "exclude_archived": "true",
        }
        if cursor:
            params["cursor"] = cursor
        async with self._client() as client:
            resp = await client.get("/api/conversations.list", params=params)
            payload = self._ok(resp, "conversations.list")
        items = [self._resource(channel) for channel in payload.get("channels", [])]
        next_cursor = (payload.get("response_metadata", {}) or {}).get("next_cursor", "") or ""
        return ResourcePage(items=items, next_cursor=next_cursor or None)

    # --- helpers ---
    def _ok(self, resp: httpx.Response, method: str) -> dict[str, Any]:
        """Parse a Slack response body and enforce the ok envelope.

        Slack returns HTTP 200 with {"ok": false, "error": "..."} for application
        errors, and HTTP 429 with a Retry-After header when rate limited. This is the
        single choke point every call goes through, so no code path can accidentally
        read data off a failed response. It raises rather than returning empty data,
        which is what makes the ACL fail closed.
        """
        if resp.status_code == 429:
            raise SlackRateLimited(method, self._retry_after(resp))
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        if not body.get("ok", False):
            raise SlackApiError(method, str(body.get("error", "unknown_error")))
        return body

    @staticmethod
    def _retry_after(resp: httpx.Response) -> int | None:
        raw = resp.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def _resource(self, channel: dict[str, Any]) -> Resource:
        channel_id = channel["id"]
        name = channel.get("name") or channel_id
        is_private = bool(channel.get("is_private", False))
        # Human label mirrors Slack's own UI convention: a hash for a public channel,
        # a lock for a private one, so the picker reads at a glance. The id stays the
        # opaque channel id the connector actually needs.
        label = f"\U0001f512 {name}" if is_private else f"# {name}"
        return Resource(
            id=channel_id,
            kind="channel",
            label=label,
            parent_id=None,
            selectable=True,
            extra={"is_private": "true" if is_private else "false"},
        )

    def _client(self) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {self._token}"}
        return httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=30.0)

    def _ref(self, channel_id: str) -> DocRef:
        return DocRef(source_doc_id=channel_id, instance_id=self.instance_id)

    async def _history_text(self, client: httpx.AsyncClient, channel_id: str) -> str:
        resp = await client.get("/api/conversations.history", params={"channel": channel_id})
        # On ok=false (channels:history/groups:history missing, or not_in_channel)
        # this raises instead of indexing an empty document, so a scope or membership
        # failure surfaces to the runtime rather than silently producing empty text.
        payload = self._ok(resp, "conversations.history")
        messages = payload.get("messages", [])
        return "\n".join(m.get("text", "") for m in messages if m.get("text"))

    async def _acl(self, client: httpx.AsyncClient, channel_id: str) -> AccessControl:
        ns = self.instance_id
        info_resp = await client.get("/api/conversations.info", params={"channel": channel_id})
        # SECURITY: on ok=false (channel_not_found, missing_scope, not_in_channel,
        # invalid_auth, token_revoked) _ok RAISES, so we never fall through to a
        # public grant for a channel the credential cannot even resolve. Fail closed.
        info = self._ok(info_resp, "conversations.info")
        channel = info.get("channel", {})
        # SECURITY: default is_private to True (private) when the field is absent. Slack
        # always returns is_private on an ok=true conversations.info, but if it is ever
        # missing we treat the channel as private, the NARROWER audience. Defaulting to
        # public here would be a fail-open leak, so we fail closed.
        if channel.get("is_private", True):
            members = [m async for m in self._members(client, channel_id)]
            # Slack exposes no per channel deny, so deny stays empty.
            return AccessControl(allow_users=members, principal_ns=ns)
        # SECURITY: a public channel is NOT world readable in Slack. Guests cannot read
        # arbitrary public channels and pansoph's public grant resolves to the tenant
        # everyone group, which can include non-Slack principals. So we map to the
        # full-member group, never public=True. The group id is namespaced per instance
        # (slack:member:{instance_id}), because the authz layer renders a group in the
        # tenant namespace and would otherwise alias two Slack workspaces in one tenant
        # onto a single node, leaking workspace A's public docs to workspace B's members.
        # This under-shares until the identity layer materializes the group, which is
        # safe; public=True would over-share, which is a leak.
        return AccessControl(allow_groups=[f"{_WORKSPACE_MEMBER_GROUP}:{ns}"], principal_ns=ns)

    async def _members(self, client: httpx.AsyncClient, channel_id: str) -> AsyncIterator[str]:
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"channel": channel_id, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            resp = await client.get("/api/conversations.members", params=params)
            # On ok=false (not_in_channel when the bot was never invited to a private
            # channel, per the conversations.members reference) _ok RAISES. We never
            # persist a truncated or empty member set as if the channel had no
            # members: an empty ACL for a channel that really has members is silent
            # under-sharing and is indistinguishable from a genuinely empty channel,
            # so we surface the error instead of returning [].
            payload = self._ok(resp, "conversations.members")
            for user_id in payload.get("members", []):
                yield user_id
            cursor = (payload.get("response_metadata", {}) or {}).get("next_cursor", "") or ""
            if not cursor:
                break
