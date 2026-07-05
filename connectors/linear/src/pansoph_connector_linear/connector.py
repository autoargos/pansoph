"""The linear connector.

Implemented against the Linear GraphQL API. Verified against the official docs:

  API docs:      https://linear.app/developers/graphql
  Pagination:    https://linear.app/developers/pagination
  Filtering:     https://linear.app/developers/filtering
  Private teams: https://linear.app/docs/private-teams
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
                 - NON-PRIVATE (workspace) team: its issues are visible to ALL
                   workspace members, not only the team's members. Within this
                   instance's ACL namespace every principal is a workspace member, so
                   the faithful mapping is public=True (workspace-visible), NOT
                   allow_groups=[team_id], which would wrongly hide the issue from
                   workspace members who are not on that team.
               Linear exposes no per-issue viewer list, so there are no per-issue
               allow_users and no deny at the issue level. The connector reads
               Team.private to make the split; without it a public-team issue would
               over-deny.

Credential scope note: every configured instance carries its own credential and its
own ACL namespace (principal_ns = instance_id). One instance may be a personal key
over one user's workspace, another an OAuth token over an org. They never collide,
and each maps ACLs at the granularity its credential grants. public=True here means
"visible to every principal in this instance's namespace" (workspace-wide), not
globally public. See pansoph CLAUDE.md section 6.

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
# team.private drives the ACL split (private team -> group scoped, public team ->
# workspace visible), so it must be fetched, not assumed.
_FETCH_QUERY = "query($id:String!){ issue(id:$id){ id title description team { id key private } } }"


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
        # Faithful to https://linear.app/docs/private-teams:
        #   private team  -> only members see it -> allow_groups=[team_id]
        #   public  team  -> all workspace members see it -> public=True
        # Reading team.private is what prevents over-denying a public-team issue.
        team: dict[str, Any] = issue.get("team") or {}
        team_id: str | None = team.get("id")
        ns = self.instance_id
        if not team.get("private", False):
            # Non-private team: visible to every workspace member, which in this
            # instance's namespace is every principal, so public=True is faithful.
            return AccessControl(public=True, principal_ns=ns)
        # Private team: closed to non-members; the team is the allowed group.
        groups: list[str] = [team_id] if team_id else []
        return AccessControl(allow_groups=groups, principal_ns=ns)
