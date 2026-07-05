"""The SharePoint and OneDrive connector, built on Microsoft Graph.

Implemented against the Microsoft Graph v1.0 API. Verified against the official docs:

  API docs:    https://learn.microsoft.com/en-us/graph/api/driveitem-delta?view=graph-rest-1.0
                 (delta: full-hierarchy enumeration, incremental cursor, deletion signal)
               https://learn.microsoft.com/en-us/graph/api/resources/deleted?view=graph-rest-1.0
                 (deleted facet: the tombstone marker on a delta item)
               https://learn.microsoft.com/en-us/graph/api/driveitem-get?view=graph-rest-1.0
                 (get item metadata)
               https://learn.microsoft.com/en-us/graph/api/driveitem-list-permissions?view=graph-rest-1.0
                 (list permissions)
               https://learn.microsoft.com/en-us/graph/api/resources/permission?view=graph-rest-1.0
                 (permission resource: grant-only, no deny facet)
               https://learn.microsoft.com/en-us/graph/api/resources/sharinglink?view=graph-rest-1.0
                 (sharingLink scope enum: anonymous, organization, existingAccess, users)
               https://learn.microsoft.com/en-us/graph/api/resources/sharepointidentityset?view=graph-rest-1.0
                 (identity sets: user, siteUser, group, siteGroup, sharePointGroup)
  API version: Graph v1.0 (base https://graph.microsoft.com/v1.0)
  Auth:        OAuth2. The connector is supplied an app-only (or delegated) access token
               via config "token"; acquiring and refreshing that token is the operator's
               step (client credentials flow against the tenant, with Files.Read.All or
               Sites.Read.All application permission). The connector sends it as
               Authorization: Bearer <token> and never sees the client secret. It maps
               whatever the credential can reach, not an assumed scope.
  Pagination:  the delta collection returns @odata.nextLink while more pages of the
               current change set remain. Follow it until it is absent, at which point
               the final page carries @odata.deltaLink instead. The runtime persists the
               deltaLink as the durable cursor and hands it back on the next sync.
  Incremental: GET /drives/{drive_id}/root/delta walks the full drive hierarchy (not just
               the root's immediate children) and is the Graph-recommended way to
               enumerate a drive completely, since paging children can miss items when
               writes happen mid-enumeration. On the first sync (cursor is None) delta
               enumerates the whole drive; on later syncs the saved @odata.deltaLink
               returns only what changed. list_changes yields the deltaLink as the cursor
               on the terminal page, so incremental=True is honest.
  Deletions:   a delta item that carries the "deleted" facet is a tombstone: per the
               delta docs, "Items with this property set should be removed from your
               local state." For each such item list_changes yields a DocRef with
               deleted=True (source_doc_id and instance_id only, content never fetched),
               so deletions=True is honest. A delta item may carry both a folder/file
               facet and a deleted facet; the deleted facet wins and the item is a
               tombstone. The same item id may appear more than once across a delta
               feed; the runtime keys by source_doc_id and the last occurrence wins.
  ACL model:   each driveItem carries a permissions collection. A sharing link with
               scope "anonymous" is public to the world, so it maps to public=True. Per
               the sharingLink docs (Scope options), scope "organization" means "anyone
               signed into your organization (tenant) can use the link to get access":
               that is a tenant member WHO HOLDS THE LINK, and it excludes B2B guests. It
               is NOT all tenant members. AccessControl is UNION-only (public OR
               allow_users OR allow_groups, minus deny) and cannot express the AND of
               "tenant member" and "holds the link", so we fail closed and map
               "organization" to a stable namespaced membership group,
               _ORG_MEMBER_GROUP, rather than to public=True (see caveat 1). Scopes
               "existingAccess" and "users" grant nothing beyond the item's existing
               per-identity grants, so they never set public.
               Direct grants live in grantedToV2 (and the grantedToIdentitiesV2 array for
               link permissions) as a SharePoint identity set: a user, group, siteUser,
               siteGroup, or sharePointGroup. Users map to allow_users, groups map to
               allow_groups. There is no deny facet in the Graph permission model (the
               resource is grant-only: grantedToV2, grantedToIdentitiesV2, link, roles),
               so deny stays empty.
  Verified on: 2026-07-05 (delta, deleted, permission, and sharingLink resources
               re-verified against the v1.0 reference on this date; the sharingLink Scope
               options table was re-read to confirm "organization" is link-bearing tenant
               members excluding guests, which drove the org-scope mapping fix below. The
               endpoints and facts are otherwise unchanged from the prior 2026-06-20
               verification).

ACL caveats (security-relevant, read before changing the mapping):
  1. "organization" scope links fail closed to a namespaced membership group, never to
     public=True. Over-sharing is a security bug; under-sharing is only a UX bug, so when
     the source expresses a "broad but conditional" audience (all tenant members AND holds
     the link, minus guests) that pansoph's UNION-only AccessControl cannot express, we map
     to the narrower side. An org-scope link becomes a grant to _ORG_MEMBER_GROUP
     ("sharepoint:org-members"), a synthetic membership group the identity layer is meant to
     materialize to the tenant's member principals (which is itself already narrower than
     the true "members who hold the link", and correctly excludes guests). Until that group
     is materialized it contains no one, so the grant reaches no principal: the document
     under-shares, which is safe. It NEVER maps to public=True, because public=True would let
     can_see() return True for every principal in the namespace, including guests and members
     who never received the link and cannot open the item in SharePoint. That is the exact
     over-share this fix closes.
  2. Source-side Deny and broken inheritance are a Graph fidelity limit, not surfaced here.
     The driveItem permissions collection is grant-only. SharePoint classic "Deny" permission
     levels and limited-access / broken-inheritance states are NOT exposed by this collection,
     so an item that carries a surfaced grant plus an unsurfaced source-side Deny will be
     over-granted relative to SharePoint. The connector cannot detect this from the Graph
     permissions API, so deny stays empty; closing it would require a different API surface
     (SharePoint REST role assignments) and is out of this connector's reach.
  3. Group expansion is deferred. A grant to a group or SharePoint group is mapped to that
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
from pansoph_connector_sdk import (
    ChangeEvent,
    ConfigField,
    ConnectorMeta,
    ConnectorSpec,
    FieldKind,
    ValidationResult,
)
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc

# Stable id of the synthetic membership group an "organization" scope sharing link maps to.
# Per the sharingLink docs, org scope is "anyone signed into your organization (tenant) can
# use the link", i.e. a tenant member WHO HOLDS THE LINK, excluding B2B guests. pansoph's
# UNION-only AccessControl cannot express that intersection, so org-scope links fail closed
# to this group instead of public=True (see the module ACL caveat 1). The identity layer is
# expected to materialize this group to the tenant's member principals; until it does, the
# group contains no one and the grant reaches no principal, so the document under-shares,
# which is safe. This id must stay stable so the identity layer can bind to it.
_ORG_MEMBER_GROUP = "sharepoint:org-members"


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

    @classmethod
    def spec(cls) -> ConnectorSpec:
        return ConnectorSpec(
            name="sharepoint",
            title="SharePoint and OneDrive",
            description=(
                "Ingest files from a SharePoint document library or a OneDrive drive over "
                "Microsoft Graph, using an app-only or delegated access token."
            ),
            fields=(
                ConfigField(
                    key="token",
                    label="Access token",
                    kind=FieldKind.SECRET,
                    help=(
                        "Microsoft Graph OAuth2 access token (Bearer). The operator mints and "
                        "refreshes it (client credentials flow, Files.Read.All or Sites.Read.All). "
                        "Sealed in the secret store."
                    ),
                ),
                ConfigField(
                    key="drive_id",
                    label="Drive ID",
                    kind=FieldKind.STRING,
                    help="The Graph drive id of the SharePoint library or OneDrive to ingest.",
                ),
                ConfigField(
                    key="base_url",
                    label="Graph base URL",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="https://graph.microsoft.com",
                    help="Override only for a sovereign cloud or a proxy. Stop before /v1.0.",
                ),
            ),
        )

    # --- contract ---
    def validate(self, config: dict[str, Any]) -> ValidationResult:
        missing = [k for k in ("token", "drive_id") if not config.get(k)]
        if missing:
            return ValidationResult(ok=False, error=f"missing config: {', '.join(missing)}")
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        """Enumerate changes via the delta feed, yielding (DocRef, cursor) per item.

        The delta endpoint walks the full drive hierarchy (not just the root's immediate
        children), so nested folders are covered, and returns tombstones through the
        "deleted" facet. cursor is None on the first sync (full enumeration) or the saved
        @odata.deltaLink URL on later syncs (changes only). Every yielded item carries the
        same cursor: the deltaLink once the terminal page is reached, else an empty string
        for items on non-terminal pages (the runtime persists a cursor only when it is a
        durable deltaLink). An item bearing the "deleted" facet is yielded as a tombstone
        (DocRef with deleted=True); its content is never fetched.
        """
        async with self._client() as client:
            # The initial delta URL, or the saved @odata.deltaLink from the last sync. Both
            # @odata.nextLink and @odata.deltaLink are absolute Graph URLs, so passing them
            # to client.get overrides base_url and preserves the opaque token verbatim.
            url: str | None = cursor or f"/v1.0/drives/{self._drive_id}/root/delta"
            while url:
                resp = await client.get(url)
                resp.raise_for_status()
                payload = resp.json()
                # deltaLink appears only on the terminal page; until then the cursor is
                # empty so the runtime does not persist a mid-set page as a resume point.
                delta_link: str = payload.get("@odata.deltaLink") or ""
                next_link: str | None = payload.get("@odata.nextLink")
                for item in payload.get("value", []):
                    item_id = item.get("id")
                    if not item_id:
                        continue
                    # The "deleted" facet marks a removed item; it wins over any file or
                    # folder facet the same delta entry may also carry.
                    is_deleted = "deleted" in item
                    yield (
                        DocRef(
                            source_doc_id=item_id,
                            instance_id=self.instance_id,
                            deleted=is_deleted,
                        ),
                        delta_link,
                    )
                url = next_link

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
            # "anonymous" is genuinely world-readable, so it is the only scope that sets
            # public=True. "organization" is link-bearing tenant members excluding guests
            # (an AND the UNION-only AccessControl cannot express), so it fails closed to a
            # namespaced membership group that under-shares until the identity layer
            # materializes it (see module ACL caveat 1). Never map org-scope to public=True.
            if scope == "anonymous":
                public = True
            elif scope == "organization":
                groups.append(_ORG_MEMBER_GROUP)
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
