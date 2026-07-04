"""The confluence connector.

Implemented against the Confluence Cloud REST API.

  API docs:    https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-content/
               https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-content-restrictions/
  Auth:        Two paths, both supported.
               (1) OAuth 2.0 (3LO): the in-app Connect flow (provider "atlassian")
                   mints a Bearer access token sealed into config key "oauth_token".
                   When present, the connector sends "Authorization: Bearer <token>".
                   Verified against
                   https://developer.atlassian.com/cloud/confluence/oauth-2-3lo-apps/
                   and the Confluence scope reference
                   https://developer.atlassian.com/cloud/confluence/scopes-for-oauth-2-3LO-and-forge-apps/
                   on 2026-07-04. Read scopes: read:confluence-content.all,
                   read:confluence-space.summary, read:content.restriction:confluence,
                   plus offline_access for refresh.
               (2) HTTP Basic (manual paste) with an Atlassian account email and an
                   API token (config: "base_url", "email", "token"). Creating the API
                   token is the operator's step.
  OAuth base:  A 3LO token is NOT scoped to a site origin. After consent the caller
               resolves the cloudid via
               https://api.atlassian.com/oauth/token/accessible-resources and
               addresses the API at
               https://api.atlassian.com/ex/confluence/{cloudid}. When an oauth_token
               is configured, set base_url to that ex/confluence/{cloudid} origin.
               This connector reads base_url as-is.
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
from pansoph_connector_sdk import (
    ChangeEvent,
    ConfigField,
    ConnectorMeta,
    ConnectorSpec,
    FieldKind,
    ValidationResult,
)
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc

# Read-only OAuth 2.0 (3LO) scopes for the in-app Connect flow (provider
# "atlassian"). Verified 2026-07-04 against the Confluence scope reference.
# read:confluence-content.all covers page bodies, read:confluence-space.summary
# covers space info, read:content.restriction:confluence covers the page read
# restrictions the ACL expands, and offline_access yields a rotating refresh token.
_OAUTH_SCOPES: tuple[str, ...] = (
    "read:confluence-content.all",
    "read:confluence-space.summary",
    "read:content.restriction:confluence",
    "offline_access",
)


class ConfluenceConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.instance_id = instance_id
        self._base = str(cfg.get("base_url", "")).rstrip("/")
        self._email = cfg.get("email", "")
        self._token = cfg.get("token", "")
        self._oauth_token = cfg.get("oauth_token", "")
        self._space = cfg.get("space_key", "")
        self.meta = ConnectorMeta(
            name="confluence",
            auth=AuthScheme.api_token,
            capabilities=Capabilities(
                incremental=True, permission_sync=True, deletions=True, slim_listing=True
            ),
        )

    @classmethod
    def spec(cls) -> ConnectorSpec:
        return ConnectorSpec(
            name="confluence",
            title="Confluence",
            description="Ingest Confluence Cloud pages using an Atlassian account and API token.",
            fields=(
                ConfigField(
                    key="base_url",
                    label="Site base URL",
                    kind=FieldKind.STRING,
                    placeholder="https://your-domain.atlassian.net",
                    help="Your Confluence Cloud site URL. The connector calls /wiki under it.",
                ),
                ConfigField(
                    key="oauth_token",
                    label="Connect with Atlassian",
                    kind=FieldKind.OAUTH,
                    required=False,
                    oauth_provider="atlassian",
                    oauth_scopes=_OAUTH_SCOPES,
                    help=(
                        "Sign in with Atlassian to mint a read-only OAuth token. "
                        "Alternative to the email plus API token below. Sealed in "
                        "the secret store."
                    ),
                ),
                ConfigField(
                    key="email",
                    label="Account email",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="you@example.com",
                    help="Atlassian account email, paired with the API token for HTTP Basic auth.",
                ),
                ConfigField(
                    key="token",
                    label="API token",
                    kind=FieldKind.SECRET,
                    required=False,
                    help=(
                        "Atlassian API token for the account. Sealed in the secret "
                        "store. Not needed if you connected with Atlassian above."
                    ),
                ),
                ConfigField(
                    key="space_key",
                    label="Space key",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="ENG",
                    help="Limit ingestion to one space. Leave blank to list all reachable pages.",
                ),
            ),
        )

    def validate(self, config: dict[str, Any]) -> ValidationResult:
        if not config.get("base_url"):
            return ValidationResult(ok=False, error="missing: base_url")
        # Auth is satisfied either by an OAuth token or by an email plus API token.
        has_oauth = bool(config.get("oauth_token"))
        has_basic = bool(config.get("email")) and bool(config.get("token"))
        if not has_oauth and not has_basic:
            return ValidationResult(
                ok=False,
                error="missing auth: connect with Atlassian, or set email and token",
            )
        return ValidationResult(ok=True)

    def _client(self) -> httpx.AsyncClient:
        # Prefer the OAuth Bearer token when the Connect flow sealed one; otherwise
        # fall back to Basic auth with the manually pasted email plus API token.
        if self._oauth_token:
            auth = f"Bearer {self._oauth_token}"
        else:
            basic = base64.b64encode(f"{self._email}:{self._token}".encode()).decode()
            auth = f"Basic {basic}"
        return httpx.AsyncClient(base_url=self._base, headers={"Authorization": auth}, timeout=30.0)

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
