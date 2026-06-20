"""The jira connector (Jira Cloud).

Implemented against the Jira Cloud platform REST API v3. Verified against the
official docs:

  API docs:    https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/
               https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/#api-rest-api-3-search-jql-post
               https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/#api-rest-api-3-issue-issueidorkey-get
               https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-project-role-actors/#api-rest-api-3-project-projectidorkey-role-id-get
               https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/ (ADF)
  Base URL:    https://{domain}.atlassian.net/rest/api/3 (config base_url is the
               site origin, e.g. "https://acme.atlassian.net").
  Auth:        Basic auth. The credential is base64("{email}:{api_token}") sent as
               "Authorization: Basic <b64>". The api_token is an Atlassian API
               token minted by the account at id.atlassian.com. The connector does
               not assume a scope; it maps whatever the credential can reach.
  Search:      IMPORTANT. The old GET/POST /rest/api/3/search is REMOVED and
               returns 410 Gone. Use POST /rest/api/3/search/jql with a JSON body
               {"jql": ..., "fields": [...], "maxResults": 100, "nextPageToken":
               <token>}. The response carries {"issues": [...], "nextPageToken":
               ..., "isLast": bool}. This is opaque token pagination: stop when
               isLast is true or nextPageToken is absent. There is no total count.
  Incremental: JQL "project = {key} ORDER BY updated ASC", adding "updated >= ..."
               when a cursor is supplied. The runtime persists the last seen
               nextPageToken (or the updated bound) as the durable cursor.
  Rate limit:  Atlassian uses a cost based rate limit with Retry-After. The runtime
               owns retries and backoff; a method only describes one page or one
               document.
  ADF:         Issue descriptions are Atlassian Document Format, a nested JSON tree.
               _adf_text walks the tree and concatenates every node["text"].
  ACL model:   best-effort, computed. A document's ACL is mapped from the project
               Browse role (the "Users" role, id 10002): actorGroup entries become
               allow_groups, actorUser entries (by accountId) become allow_users.
               This is namespaced by the instance (principal_ns = instance_id).
               LIMITATION: this is a best-effort mapping of the project role only.
               It does not resolve Jira issue security levels, which can restrict
               an individual issue below its project. An issue under a security
               level may therefore be over-shared by this mapping. There is no
               deny concept in this mapping.
  Verified on: 2026-06-20.

Credential scope note: every configured instance carries its own credential and its
own ACL namespace (principal_ns = instance_id), so two instances never collide. See
pansoph CLAUDE.md section 6.

The runtime owns retries, backoff, and resumable sync, so a method only describes
how to talk to Jira for one page or one document.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pansoph_connector_sdk import ChangeEvent, ConnectorMeta, ValidationResult
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc

# The project "Users" role, which carries Browse access in a default Jira project.
# We map this role's actors to the document ACL as a best-effort approximation of
# who can see the project's issues.
_BROWSE_ROLE_ID = "10002"


def _adf_text(node: Any) -> str:
    """Flatten an Atlassian Document Format tree into plain text.

    ADF is a nested JSON document where text lives on leaf nodes of type "text".
    This walks the whole tree and concatenates every node["text"] it finds, in
    document order, separated by spaces.
    """
    parts: list[str] = []
    if isinstance(node, dict):
        text = node.get("text")
        if isinstance(text, str):
            parts.append(text)
        for child in node.get("content", []) or []:
            child_text = _adf_text(child)
            if child_text:
                parts.append(child_text)
    elif isinstance(node, list):
        for child in node:
            child_text = _adf_text(child)
            if child_text:
                parts.append(child_text)
    return " ".join(parts)


class JiraConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        self.instance_id = instance_id
        cfg = config or {}
        self._base_url: str = cfg.get("base_url", "")
        self._email: str = cfg.get("email", "")
        self._api_token: str = cfg.get("api_token", "")
        self._project_key: str = cfg.get("project_key", "")
        self.meta = ConnectorMeta(
            name="jira",
            auth=AuthScheme.api_token,
            capabilities=Capabilities(
                incremental=True, permission_sync=True, deletions=False, slim_listing=True
            ),
        )

    # --- contract ---
    def validate(self, config: dict[str, Any]) -> ValidationResult:
        missing = [
            k for k in ("base_url", "email", "api_token", "project_key") if not config.get(k)
        ]
        if missing:
            return ValidationResult(ok=False, error=f"missing config: {', '.join(missing)}")
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        jql = f"project = {self._project_key} ORDER BY updated ASC"
        if cursor:
            # The cursor is a JQL updated bound. Re-anchor the window so a resumed
            # sync only revisits issues touched at or after the last sync point.
            jql = f"project = {self._project_key} AND updated >= '{cursor}' ORDER BY updated ASC"
        async with self._client() as client:
            page_token: str | None = None
            while True:
                body: dict[str, Any] = {
                    "jql": jql,
                    "fields": ["summary"],
                    "maxResults": 100,
                }
                if page_token:
                    body["nextPageToken"] = page_token
                resp = await client.post("/rest/api/3/search/jql", json=body)
                resp.raise_for_status()
                payload = resp.json()
                page_token = payload.get("nextPageToken")
                next_cursor = page_token or ""
                for issue in payload.get("issues", []):
                    key = issue.get("key", "")
                    if key:
                        yield DocRef(source_doc_id=key, instance_id=self.instance_id), next_cursor
                if payload.get("isLast", False) or not page_token:
                    break

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        async with self._client() as client:
            resp = await client.get(
                f"/rest/api/3/issue/{ref.source_doc_id}",
                params={"fields": "summary,description"},
            )
            resp.raise_for_status()
            fields = resp.json().get("fields", {})
            summary = fields.get("summary") or ""
            description = _adf_text(fields.get("description"))
            text = summary if not description else f"{summary}\n\n{description}"
            raw = RawDoc(
                source_doc_id=ref.source_doc_id,
                instance_id=self.instance_id,
                mime="text/plain",
                text=text,
                title=summary,
            )
            return raw, await self._acl(client)

    async def list_acls(self, ref: DocRef) -> AccessControl:
        # ACL is computed at the project Browse role granularity, so the specific
        # issue key is intentionally ignored. See the module limitation note.
        async with self._client() as client:
            return await self._acl(client)

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        return []

    # --- helpers ---
    def _client(self) -> httpx.AsyncClient:
        token = base64.b64encode(f"{self._email}:{self._api_token}".encode()).decode("ascii")
        headers = {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
        }
        return httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=30.0)

    async def _acl(self, client: httpx.AsyncClient) -> AccessControl:
        resp = await client.get(f"/rest/api/3/project/{self._project_key}/role/{_BROWSE_ROLE_ID}")
        resp.raise_for_status()
        actors = resp.json().get("actors", [])
        # Build allow_groups first, then allow_users, matching the actors order, so
        # the produced ACL is deterministic for conformance.
        allow_groups: list[str] = []
        allow_users: list[str] = []
        for actor in actors:
            group = actor.get("actorGroup")
            if group and group.get("name"):
                allow_groups.append(group["name"])
        for actor in actors:
            user = actor.get("actorUser")
            if user and user.get("accountId"):
                allow_users.append(user["accountId"])
        return AccessControl(
            allow_groups=allow_groups,
            allow_users=allow_users,
            principal_ns=self.instance_id,
        )
