"""The confluence connector.

Implemented against the Confluence Cloud REST API.

  API docs:    https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-content/
               https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-content-restrictions/
  Auth:        HTTP Basic with an Atlassian account email and an API token
               (config: "base_url", "email", "token"). Creating the API token is the
               operator's step; this connector does not run an OAuth flow.
  Incremental: paginate content with start/limit; the version timestamp drives
               freshness (full re-list for the demo).
  ACL model:   a page's read restrictions map to the ACL. Restricted users become
               allow_users (accountId), restricted groups become allow_groups. A page
               with no read restriction inherits space access, mapped here to a
               space-scoped group. Namespaced by the connector instance.
  Verified:    against the documented v1 content endpoints; re-verify and provide an
               API token to run it live.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pansoph_connector_sdk import ChangeEvent, ConnectorMeta, ValidationResult
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc


class ConfluenceConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.instance_id = instance_id
        self._base = str(cfg.get("base_url", "")).rstrip("/")
        self._email = cfg.get("email", "")
        self._token = cfg.get("token", "")
        self._space = cfg.get("space_key", "")
        self.meta = ConnectorMeta(
            name="confluence",
            auth=AuthScheme.api_token,
            capabilities=Capabilities(
                incremental=True, permission_sync=True, deletions=True, slim_listing=True
            ),
        )

    def validate(self, config: dict[str, Any]) -> ValidationResult:
        missing = [k for k in ("base_url", "email", "token") if not config.get(k)]
        if missing:
            return ValidationResult(ok=False, error=f"missing: {', '.join(missing)}")
        return ValidationResult(ok=True)

    def _client(self) -> httpx.AsyncClient:
        basic = base64.b64encode(f"{self._email}:{self._token}".encode()).decode()
        return httpx.AsyncClient(
            base_url=self._base, headers={"Authorization": f"Basic {basic}"}, timeout=30.0
        )

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        async with self._client() as client:
            start = 0
            while True:
                params: dict[str, Any] = {"type": "page", "limit": 100, "start": start}
                if self._space:
                    params["spaceKey"] = self._space
                resp = await client.get("/wiki/rest/api/content", params=params)
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                for page in results:
                    yield DocRef(source_doc_id=page["id"], instance_id=self.instance_id), ""
                if len(results) < 100:
                    break
                start += 100

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        async with self._client() as client:
            resp = await client.get(
                f"/wiki/rest/api/content/{ref.source_doc_id}",
                params={
                    "expand": (
                        "body.storage,restrictions.read.restrictions.user,"
                        "restrictions.read.restrictions.group,space"
                    )
                },
            )
            resp.raise_for_status()
            page = resp.json()
            body = page.get("body", {}).get("storage", {}).get("value", "")
            raw = RawDoc(
                source_doc_id=ref.source_doc_id,
                instance_id=self.instance_id,
                mime="text/html",
                text=body,
                title=page.get("title"),
                source_url=f"{self._base}/wiki/pages/viewpage.action?pageId={ref.source_doc_id}",
            )
            return raw, self._acl_from_page(page)

    async def list_acls(self, ref: DocRef) -> AccessControl:
        async with self._client() as client:
            resp = await client.get(
                f"/wiki/rest/api/content/{ref.source_doc_id}",
                params={
                    "expand": (
                        "restrictions.read.restrictions.user,"
                        "restrictions.read.restrictions.group,space"
                    )
                },
            )
            resp.raise_for_status()
            return self._acl_from_page(resp.json())

    def _acl_from_page(self, page: dict[str, Any]) -> AccessControl:
        read = page.get("restrictions", {}).get("read", {}).get("restrictions", {})
        users = [
            u["accountId"] for u in read.get("user", {}).get("results", []) if u.get("accountId")
        ]
        groups = [g["name"] for g in read.get("group", {}).get("results", []) if g.get("name")]
        if not users and not groups:
            # No explicit read restriction: scope to the space.
            space = page.get("space", {}).get("key") or self._space
            groups = [f"space:{space}"] if space else []
        return AccessControl(allow_users=users, allow_groups=groups, principal_ns=self.instance_id)

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        return []
