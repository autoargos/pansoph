"""The linear connector.

Implemented against the Linear GraphQL API. Verified against the official docs:

  API docs:      https://linear.app/developers/graphql
  Pagination:    https://linear.app/developers/pagination
  Filtering:     https://linear.app/developers/filtering
  Private teams: https://linear.app/docs/private-teams
  Member roles:  https://linear.app/docs/members-roles
  Guest accounts:https://linear.app/changelog/2022-07-14-guest-accounts
  Webhooks:      https://linear.app/developers/webhooks
                 https://linear.app/docs/api-and-webhooks
  Verified on:   2026-07-05.

  Endpoint:    https://api.linear.app/graphql (single GraphQL endpoint). Every
               request is a POST of JSON {"query": ..., "variables": ...}.
  Auth:        API key in the Authorization header. A personal API key is sent
               WITHOUT a "Bearer " prefix (Authorization: <api_key>). OAuth access
               tokens use the Bearer prefix. This connector takes the credential
               from config "token" and sends it verbatim as {"Authorization": token},
               so a caller passes a personal key as is, or "Bearer <token>" for OAuth.
  Pagination:  GraphQL connections expose pageInfo with endCursor and hasNextPage.
               A continuation passes the endCursor as the `after` argument. This
               connector performs a full listing each run by paging GraphQL
               connections and yields each issue's updatedAt as its per-doc cursor
               (a durable, non-empty resume token).
  Incremental: not supported here (Capabilities.incremental = False), and honestly
               declared as such. Linear DOES expose the raw materials for it:
               IssueFilter supports date comparators (updatedAt: {gt: ...}) per the
               filtering docs, and issues can be ordered by updatedAt per the
               pagination docs, so a future revision can turn the incoming cursor
               into filter:{updatedAt:{gt: <cursor>}}. Until that lands the incoming
               cursor is ignored and the connector re-lists in full.
  Deletions:   not supported here (Capabilities.deletions = False), and honestly
               declared as such. A full re-list cannot observe a hard delete because
               a deleted issue simply stops appearing, so this connector never emits
               a deleted=True DocRef. Linear's faithful deletion signal is the
               webhook "remove" action (https://linear.app/developers/webhooks),
               which is not retroactive; wiring on_webhook to translate remove events
               into tombstones is the follow-up path. on_webhook is a stub today.
  ACL model:   team membership is the real access boundary, and it splits on
               Team.private (https://linear.app/docs/private-teams):
                 - PRIVATE team: "Those who are not a member of the private team will
                   not be able to see issues associated with the team." Only the
                   team's members may see the issue, so the ACL is
                   allow_groups=[team_id] (the private team acts as the closed group).
                 - OPEN (non-private) team: its issues are visible to every FULL
                   workspace member (Owner / Admin / Team Owner / Member), whether or
                   not they have joined that team. It is NOT visible to every
                   workspace PRINCIPAL, because Linear Guests are workspace principals
                   too (paid seats that appear in member management, per
                   https://linear.app/docs/members-roles) yet a guest "will not be
                   able to see or discover any other teams in the workspace or have
                   access to those issues" (guest-accounts changelog, 2022-07-14).
                   So an open team's real audience is "all full members but not
                   guests." pansoph's AccessControl is UNION-only and cannot express
                   that as public=True: public=True short-circuits can_see to True for
                   EVERY principal in the namespace, which grants the issue to guests
                   who cannot read it in Linear (an over-share / fail-open leak). Per
                   pansoph CLAUDE.md rule 1, over-sharing is a security bug, so we fail
                   closed and map an open team to the namespaced full-members group
                   _WORKSPACE_MEMBERS_GROUP instead of public=True. That group under-
                   shares (grants no one) until the identity layer materializes it with
                   the workspace's full members (excluding guests), which is the safe
                   direction. See _acl.
               Linear exposes no per-issue viewer list, so there are no per-issue
               allow_users and no deny at the issue level. The connector reads
               Team.private to make the split; without it an open-team issue would
               over-deny.

Credential scope note: every configured instance carries its own credential and its
own ACL namespace (principal_ns = instance_id). One instance may be a personal key
over one user's workspace, another an OAuth token over an org. They never collide,
and each maps ACLs at the granularity its credential grants. Group ids (a private
team_id, or the reserved _WORKSPACE_MEMBERS_GROUP for open teams) are resolved within
this instance's namespace only, never globally. This connector never emits public=True
for a Linear issue: no Linear issue is visible to every workspace principal, because
guests are principals who are excluded. See pansoph CLAUDE.md section 6 and rule 1.

The runtime owns retries, backoff, and resumable sync, so a method only describes
how to talk to Linear for one page or one document.
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

# updatedAt is selected so list_changes can yield it as a durable, non-empty
# per-doc cursor. orderBy updatedAt keeps the walk deterministic.
_LIST_QUERY = (
    "query($after: String) { issues(first: 100, after: $after, orderBy: updatedAt) "
    "{ nodes { id title updatedAt } pageInfo { hasNextPage endCursor } } }"
)
# team.private drives the ACL split (private team -> team group, open team ->
# full-members group), so it must be fetched, not assumed.
_FETCH_QUERY = "query($id:String!){ issue(id:$id){ id title description team { id key private } } }"

# Stable, reserved, namespaced id for a Linear workspace's FULL members, i.e. every
# principal EXCEPT guests (Owner / Admin / Team Owner / Member per
# https://linear.app/docs/members-roles). An OPEN (non-private) team's issues are
# visible to full members but NOT to guests (guest-accounts changelog, 2022-07-14:
# a guest "will not be able to see or discover any other teams in the workspace or
# have access to those issues"). pansoph's UNION-only AccessControl cannot express
# "all full members but not guests" as public=True without leaking to guest
# principals, so an open team maps to this group instead (fail closed, CLAUDE.md rule
# 1). The identity layer MUST materialize this group with the workspace's full members
# (excluding guests); until it does, the group grants no one, so the issue under-shares,
# which is the safe direction. The "linear:" prefix and this reserved suffix cannot
# collide with a Linear team id (a UUID), so it never shadows a real team group.
_WORKSPACE_MEMBERS_GROUP = "linear:workspace-members"


class LinearConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        self.instance_id = instance_id
        cfg = config or {}
        self._token: str = cfg.get("token", "")
        # base_url override points at the host; the GraphQL path is always /graphql.
        self._base_url: str = cfg.get("base_url", "https://api.linear.app")
        self.meta = ConnectorMeta(
            name="linear",
            auth=AuthScheme.api_token,
            capabilities=Capabilities(
                incremental=False, permission_sync=True, deletions=False, slim_listing=True
            ),
        )

    @classmethod
    def spec(cls) -> ConnectorSpec:
        return ConnectorSpec(
            name="linear",
            title="Linear",
            description="Ingest issues from a Linear workspace over the GraphQL API.",
            fields=(
                ConfigField(
                    key="token",
                    label="API key",
                    kind=FieldKind.SECRET,
                    help=(
                        "A Linear personal API key, or 'Bearer <token>' for an OAuth access "
                        "token. Sent verbatim as the Authorization header. Sealed in the "
                        "secret store."
                    ),
                ),
                ConfigField(
                    key="base_url",
                    label="API base URL",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="https://api.linear.app",
                    help=(
                        "Override only for a proxy or self-managed gateway. The path is "
                        "always /graphql."
                    ),
                ),
            ),
        )

    # --- contract ---
    def validate(self, config: dict[str, Any]) -> ValidationResult:
        if not config.get("token"):
            return ValidationResult(ok=False, error="missing config: token")
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        # This connector is not incremental (Capabilities.incremental = False): the
        # incoming cursor is ignored and paging starts at the head, walking pageInfo
        # to the end every run. The per-doc cursor yielded here is the issue's
        # updatedAt, a durable, non-empty resume token that also seeds the future
        # filter:{updatedAt:{gt}} incremental path. It falls back to the issue id if
        # updatedAt is somehow absent, so the non-empty-cursor contract always holds.
        async with self._client() as client:
            after: str | None = None
            while True:
                data = await self._graphql(client, _LIST_QUERY, {"after": after})
                issues = data.get("issues", {})
                for node in issues.get("nodes", []):
                    node_id: str = node["id"]
                    doc_cursor = node.get("updatedAt") or node_id
                    yield self._ref(node_id), doc_cursor
                page = issues.get("pageInfo", {})
                if not page.get("hasNextPage"):
                    break
                after = page.get("endCursor")

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        async with self._client() as client:
            issue = await self._issue(client, ref.source_doc_id)
            title = issue.get("title") or ""
            description = issue.get("description") or ""
            text = "\n\n".join(part for part in (title, description) if part)
            raw = RawDoc(
                source_doc_id=ref.source_doc_id,
                instance_id=self.instance_id,
                mime="text/plain",
                text=text,
                title=title,
            )
            return raw, self._acl(issue)

    async def list_acls(self, ref: DocRef) -> AccessControl:
        # Re fetch the issue to read its team id. The ACL must not depend on any
        # state left by list_changes, so the team is resolved here directly.
        async with self._client() as client:
            issue = await self._issue(client, ref.source_doc_id)
            return self._acl(issue)

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        # Not wired up yet, and deletions is declared False to match. Linear's
        # faithful deletion signal is the webhook "remove" action
        # (https://linear.app/developers/webhooks), which is not retroactive.
        # Translating create/update/remove events into ChangeEvents (including a
        # deleted tombstone for "remove") is the follow-up path that would let
        # deletions flip to True honestly.
        return []

    # --- helpers ---
    def _client(self) -> httpx.AsyncClient:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = self._token
        return httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=30.0)

    def _ref(self, source_doc_id: str) -> DocRef:
        return DocRef(source_doc_id=source_doc_id, instance_id=self.instance_id)

    async def _graphql(
        self, client: httpx.AsyncClient, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"query": query}
        if variables is not None:
            body["variables"] = variables
        resp = await client.post("/graphql", json=body)
        resp.raise_for_status()
        return resp.json().get("data", {}) or {}

    async def _issue(self, client: httpx.AsyncClient, issue_id: str) -> dict[str, Any]:
        data = await self._graphql(client, _FETCH_QUERY, {"id": issue_id})
        return data.get("issue", {}) or {}

    def _acl(self, issue: dict[str, Any]) -> AccessControl:
        # Faithful to https://linear.app/docs/private-teams and the guest role
        # (https://linear.app/docs/members-roles, guest-accounts changelog 2022-07-14):
        #   private team -> only its members see it     -> allow_groups=[team_id]
        #   open    team -> every FULL member (not guests) sees it
        #                                                -> allow_groups=[members group]
        # An open team is NEVER public=True: guests are workspace principals who cannot
        # see open teams, and public=True would leak the issue to them. Mapping to the
        # full-members group fails closed (it under-shares until the identity layer
        # materializes the group), per CLAUDE.md rule 1. Reading team.private is what
        # prevents over-denying an open-team issue to full members not on the team.
        team: dict[str, Any] = issue.get("team") or {}
        team_id: str | None = team.get("id")
        ns = self.instance_id
        if not team_id:
            # No resolvable team: a null/inaccessible issue, a deleted issue, or a
            # GraphQL partial error returning data.issue=null under HTTP 200. Fail
            # closed. Granting the open-team members group here would leak a private or
            # unreadable issue to every full member, so an unresolvable issue grants
            # no one.
            return AccessControl(allow_groups=[], principal_ns=ns)
        if team.get("private", True):
            # Private team (also the default when the flag is absent, failing closed on
            # the privacy axis): closed to non-members; the team is the allowed group.
            return AccessControl(allow_groups=[team_id], principal_ns=ns)
        # Open (non-private) team: visible to full workspace members but not guests.
        # Map to the reserved full-members group, never public=True.
        return AccessControl(allow_groups=[_WORKSPACE_MEMBERS_GROUP], principal_ns=ns)
