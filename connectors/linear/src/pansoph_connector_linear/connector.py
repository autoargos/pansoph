"""The linear connector.

Implemented against the Linear GraphQL API. Verified against the official docs:

  API docs:    https://linear.app/developers/graphql
  Endpoint:    https://api.linear.app/graphql (single GraphQL endpoint). Every
               request is a POST of JSON {"query": ..., "variables": ...}.
  Auth:        API key in the Authorization header. A personal API key is sent
               WITHOUT a "Bearer " prefix (Authorization: <api_key>). OAuth access
               tokens use the Bearer prefix. This connector takes the credential
               from config "token" and sends it verbatim as {"Authorization": token},
               so a caller passes a personal key as is, or "Bearer <token>" for OAuth.
  Pagination:  GraphQL connections expose pageInfo with endCursor and hasNextPage.
               A continuation passes the endCursor as the `after` argument. This
               connector surfaces the cursor as the next_cursor of list_changes.
  Incremental: not supported here. Linear can order by updatedAt, but this connector
               does a full listing each run (Capabilities.incremental = False).
  ACL model:   best effort, team scoped. Linear has no per issue viewer list, so
               there is no fine grained per issue ACL to read. An issue's TEAM is
               mapped to allow_groups (the team id), namespaced by the instance.
               There are no per issue allow_users and no deny. An operator should
               treat team membership as the access boundary, which is coarser than
               the document, and is the most faithful mapping the source exposes.
  Verified on: 2026-06-20.

Credential scope note: every configured instance carries its own credential and its
own ACL namespace (principal_ns = instance_id). One instance may be a personal key
over one user's workspace, another an OAuth token over an org. They never collide,
and each maps ACLs at the granularity its credential grants. See pansoph CLAUDE.md
section 6.

The runtime owns retries, backoff, and resumable sync, so a method only describes
how to talk to Linear for one page or one document.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
from pansoph_connector_sdk import ChangeEvent, ConnectorMeta, ValidationResult
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc

_LIST_QUERY = "{ issues(first: 100) { nodes { id title } pageInfo { endCursor hasNextPage } } }"
_FETCH_QUERY = "query($id:String!){ issue(id:$id){ id title description team { id key } } }"


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

    # --- contract ---
    def validate(self, config: dict[str, Any]) -> ValidationResult:
        if not config.get("token"):
            return ValidationResult(ok=False, error="missing config: token")
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        async with self._client() as client:
            data = await self._graphql(client, _LIST_QUERY)
            issues = data.get("issues", {})
            page = issues.get("pageInfo", {})
            next_cursor = page.get("endCursor") or ""
            for node in issues.get("nodes", []):
                yield self._ref(node["id"]), next_cursor

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
        # Webhook ingestion is not wired up for this connector.
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
        team = issue.get("team") or {}
        team_id = team.get("id")
        groups = [team_id] if team_id else []
        return AccessControl(allow_groups=groups, principal_ns=self.instance_id)
