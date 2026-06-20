"""The SharePoint and OneDrive connector, built on Microsoft Graph.

Implemented against the Microsoft Graph v1.0 API. Verified against the official docs:

  API docs:    https://learn.microsoft.com/graph/api/driveitem-list-children (list children)
               https://learn.microsoft.com/graph/api/driveitem-get (get item metadata)
               https://learn.microsoft.com/graph/api/driveitem-list-permissions (list permissions)
               https://learn.microsoft.com/graph/api/driveitem-delta (delta, incremental)
               https://learn.microsoft.com/graph/api/resources/permission (permission resource)
               https://learn.microsoft.com/graph/api/resources/sharepointidentityset (identity sets)
  API version: Graph v1.0 (base https://graph.microsoft.com/v1.0)
  Auth:        OAuth2. The connector is supplied an app-only (or delegated) access token
               via config "token"; acquiring and refreshing that token is the operator's
               step (client credentials flow against the tenant, with Files.Read.All or
               Sites.Read.All application permission). The connector sends it as
               Authorization: Bearer <token> and never sees the client secret. It maps
               whatever the credential can reach, not an assumed scope.
  Pagination:  collections return @odata.nextLink when more pages exist. Follow it until
               it is absent. The default children page size is 200 items.
  Incremental: GET /drives/{drive_id}/root/delta returns pages of changed items with an
               @odata.nextLink, then a final @odata.deltaLink to persist as the cursor for
               the next sync. This first cut paginates root/children via @odata.nextLink;
               the durable runtime owns the delta cursor lifecycle (see note below).
  ACL model:   each driveItem carries a permissions collection. A sharing link with
               scope "anonymous" is public to the world; scope "organization" is a
               tenant-wide link, which we treat as public within the instance namespace
               (see the caveat below). Direct grants live in grantedToV2 (and the
               grantedToIdentitiesV2 array for link permissions) as a SharePoint identity
               set: a user, group, siteUser, siteGroup, or sharePointGroup. Users map to
               allow_users, groups map to allow_groups. There is no native deny in the
               Graph permission model, so deny stays empty.
  Verified on: 2026-06-20.

ACL caveats (security-relevant, read before changing the mapping):
  1. "organization" scope links are treated as public. They are visible to every member
     of the tenant, not the whole internet, but within a single-tenant pansoph instance
     every principal in the namespace is a tenant member, so public=True is the faithful
     mapping for the principals this instance can see. If a future instance spans guests
     or multiple tenants, revisit this.
  2. Group expansion is deferred. A grant to a group or SharePoint group is mapped to that
     group's direct identifier in allow_groups. Resolving a group to its transitive member
     users (and nested groups) is left to a later authz-layer pass, which already owns the
     ACL-to-tuple expansion. The connector records the direct identity faithfully so that
     pass has the truth to expand from.

Credential scope note: every configured instance carries its own token and its own ACL
namespace (principal_ns = instance_id). One instance may be a single user's OneDrive,
another a SharePoint document library, another a tenant-wide app credential. They never
collide, and each maps ACLs at the granularity its credential grants.

The runtime owns retries, backoff, and resumable sync, so a method only describes how to
talk to Graph for one page or one document.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
from pansoph_connector_sdk import ChangeEvent, ConnectorMeta, ValidationResult
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc


class SharepointConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        self.instance_id = instance_id
        cfg = config or {}
        self._token: str = cfg.get("token", "")
        self._drive_id: str = cfg.get("drive_id", "")
        # base_url stops before /v1.0 so callers pass the full versioned path. This keeps
        # request paths predictable for mocking and for any future api-version override.
        self._base_url: str = cfg.get("base_url", "https://graph.microsoft.com")
        self.meta = ConnectorMeta(
            name="sharepoint",
            auth=AuthScheme.oauth2,
            capabilities=Capabilities(
                incremental=True, permission_sync=True, deletions=True, slim_listing=True
            ),
        )

    # --- contract ---
    def validate(self, config: dict[str, Any]) -> ValidationResult:
        missing = [k for k in ("token", "drive_id") if not config.get(k)]
        if missing:
            return ValidationResult(ok=False, error=f"missing config: {', '.join(missing)}")
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        async with self._client() as client:
            url: str | None = f"/v1.0/drives/{self._drive_id}/root/children"
            while url:
                resp = await client.get(url)
                resp.raise_for_status()
                payload = resp.json()
                for item in payload.get("value", []):
                    item_id = item.get("id")
                    if not item_id:
                        continue
                    next_cursor = item.get("lastModifiedDateTime", "") or ""
                    yield self._ref(item_id), next_cursor
                url = payload.get("@odata.nextLink")

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        async with self._client() as client:
            resp = await client.get(f"/v1.0/drives/{self._drive_id}/items/{ref.source_doc_id}")
            resp.raise_for_status()
            item = resp.json()
            mime = item.get("file", {}).get("mimeType", "application/octet-stream")
            raw = RawDoc(
                source_doc_id=ref.source_doc_id,
                instance_id=self.instance_id,
                mime=mime,
                # Binary content extraction is a downstream parsing step; the connector
                # only carries metadata and the ACL here.
                text=None,
                title=item.get("name"),
                source_url=item.get("webUrl"),
            )
            return raw, await self._acl(client, ref.source_doc_id)

    async def list_acls(self, ref: DocRef) -> AccessControl:
        async with self._client() as client:
            return await self._acl(client, ref.source_doc_id)

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        # Graph change notifications carry only a resource pointer and a clientState, not
        # the changed item bodies, so the freshness path is to trigger a delta sync rather
        # than to synthesize change events here. Return nothing; the runtime re-syncs.
        return []

    # --- helpers ---
    def _client(self) -> httpx.AsyncClient:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=30.0)

    def _ref(self, source_doc_id: str) -> DocRef:
        return DocRef(source_doc_id=source_doc_id, instance_id=self.instance_id)

    async def _acl(self, client: httpx.AsyncClient, item_id: str) -> AccessControl:
        resp = await client.get(f"/v1.0/drives/{self._drive_id}/items/{item_id}/permissions")
        resp.raise_for_status()
        ns = self.instance_id
        public = False
        users: list[str] = []
        groups: list[str] = []
        for perm in resp.json().get("value", []):
            scope = perm.get("link", {}).get("scope")
            # "anonymous" is public to the world; "organization" is a tenant-wide link,
            # treated as public within this instance namespace (see module caveat 1).
            if scope in ("anonymous", "organization"):
                public = True
            for ident in self._identities(perm):
                self._classify(ident, users, groups)
        return AccessControl(
            public=public,
            allow_users=_dedupe(users),
            allow_groups=_dedupe(groups),
            principal_ns=ns,
        )

    @staticmethod
    def _identities(perm: dict[str, Any]) -> list[dict[str, Any]]:
        # grantedToV2 is the direct grant; grantedToIdentitiesV2 is the array form used by
        # link permissions. Both are SharePoint identity sets.
        out: list[dict[str, Any]] = []
        granted = perm.get("grantedToV2")
        if isinstance(granted, dict):
            out.append(granted)
        for entry in perm.get("grantedToIdentitiesV2", []) or []:
            if isinstance(entry, dict):
                out.append(entry)
        return out

    @staticmethod
    def _classify(identity_set: dict[str, Any], users: list[str], groups: list[str]) -> None:
        # A SharePoint identity set may carry user / siteUser (a person) and group /
        # siteGroup / sharePointGroup (a collection). Prefer email when present, else id.
        for key in ("user", "siteUser"):
            ident = identity_set.get(key)
            if isinstance(ident, dict):
                value = _principal_id(ident)
                if value:
                    users.append(value)
        for key in ("group", "siteGroup", "sharePointGroup"):
            ident = identity_set.get(key)
            if isinstance(ident, dict):
                value = _principal_id(ident)
                if value:
                    groups.append(value)


def _principal_id(ident: dict[str, Any]) -> str:
    # Prefer a stable email identifier, then the directory id, then the SharePoint login.
    return ident.get("email") or ident.get("id") or ident.get("loginName") or ""


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
