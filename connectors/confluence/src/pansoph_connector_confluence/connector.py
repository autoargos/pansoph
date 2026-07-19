"""The confluence connector (Confluence Cloud).

Implemented against the Confluence Cloud REST API. Verified 2026-07-06 against the
official Atlassian developer docs and support KBs (re-verify on every change; the
docs win over this code):

  Content + search:
    https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-content/#api-wiki-rest-api-content-search-get
    https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-content/#api-wiki-rest-api-content-id-get
  Content restrictions (read):
    https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-content-restrictions/
  CQL (incremental change feed):
    https://developer.atlassian.com/cloud/confluence/advanced-searching-using-cql/
    https://developer.atlassian.com/cloud/confluence/using-the-rest-api/
  Inherited restrictions (the ACL cascade the API does NOT return directly):
    https://support.atlassian.com/confluence/kb/confluence-get-page-restrictions-api-doesnt-display-inherited-restrictions/
    https://confluence.atlassian.com/doc/page-restrictions-139414.html
  Restriction AND space-View interaction (a page restriction can only NARROW,
  never grant beyond the space; verified 2026-07-06):
    https://support.atlassian.com/confluence-cloud/docs/what-are-confluence-cloud-permissions-and-restrictions/
    https://support.atlassian.com/confluence-cloud/docs/manage-permissions-on-the-page-level/
  Trashed content (the deletion signal; CQL cannot search trash):
    https://support.atlassian.com/confluence/kb/how-to-purge-all-remove-all-trash-in-a-space-using-rest-api/
  Spaces + space permissions (the space-wide view audience):
    https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-space/#api-spaces-get
    https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-space-permissions/
    https://developer.atlassian.com/cloud/confluence/rest/v2/intro/#pagination
  OAuth 2.0 (3LO) + scopes:
    https://developer.atlassian.com/cloud/confluence/oauth-2-3lo-apps/
    https://developer.atlassian.com/cloud/confluence/scopes-for-oauth-2-3LO-and-forge-apps/

Auth
  Two paths, both supported.
  (1) OAuth 2.0 (3LO): the in-app Connect flow (provider "atlassian") mints a Bearer
      access token sealed into config key "oauth_token". When present, the connector
      sends "Authorization: Bearer <token>". Read scopes: search:confluence (CQL change
      feed), read:page:confluence (page content, granular) plus read:confluence-content.all
      (its classic equivalent), read:confluence-space.summary, read:content.restriction:confluence
      (page restrictions), read:space:confluence (space discovery),
      read:space.permission:confluence (space-wide View audience), plus offline_access for
      refresh. See _OAUTH_SCOPES for the endpoint mapping and the granular/classic story.
  (2) HTTP Basic (manual paste): base64("{email}:{api_token}") sent as
      "Authorization: Basic <b64>". The API token is minted at id.atlassian.com.
  A configured instance carries its own credential and its own ACL namespace
  (principal_ns = instance_id), so two instances never collide. The connector does
  not assume a scope; it maps whatever the credential can actually reach.

OAuth base
  A 3LO token is NOT scoped to a site origin. After consent the connector resolves
  the cloud id itself from the token (GET
  https://api.atlassian.com/oauth/token/accessible-resources) and addresses the API
  at https://api.atlassian.com/ex/confluence/{cloud_id}, so an OAuth instance needs
  no base_url (see _ensure_base). A multi-site grant is disambiguated by the optional
  cloud_id or site_url config fields, else the first accessible site is used. A
  manual Server/DC or basic-auth Cloud instance still sets base_url directly.

Incremental (list_changes, upsert phase)
  A real high-water-mark cursor over the CQL lastmodified field. The connector calls
  GET /wiki/rest/api/content/search with cql "type=page [AND space=\"KEY\"] AND
  lastmodified >= \"<floor>\" ORDER BY lastmodified ASC", expanding only version (no
  body, so the 25-result body cap never applies), and follows cursor pagination via
  _links.next. CQL lastmodified has minute granularity, so the query floor is the
  cursor truncated to the minute (a safe over-fetch), and the connector then filters
  precisely in code on version.when (full ISO-8601), skipping any page at or before
  the exact cursor. Each yielded cursor is that page's version.when, so a crash
  resumes at the last checkpointed page and a replay of the last cursor yields no
  already-seen page. Trash cannot be searched with CQL, so deletions are a separate
  phase (below).

Deletions (list_changes, tombstone phase)
  Trashed pages are the deletion signal. CQL cannot search trash, so the connector
  polls GET /wiki/api/v2/pages?status=trashed (cursor pagination), scoped to the
  configured space by numeric space-id when one is set, and yields a
  DocRef(deleted=True) per trashed page. Tombstones carry the current upsert checkpoint
  so the persisted cursor stays a valid lastmodified bound. (The v1 content list is not
  used: it 401s under a granular token, like all v1 content endpoints.)

ACL model (the security-critical part)
  Confluence VIEW is a conjunction of independent gates. To view a page a user must:
  (a) satisfy the page's own read restriction, AND (b) satisfy every ancestor's read
  restriction (restrictions cascade to descendants), AND (c) hold the space View
  permission. Atlassian is explicit that a page restriction can only NARROW access
  within the space, never grant beyond it: "a content item cannot have greater access
  than its container" and "if you say a person can view in the restrictions dialog and
  they don't have view permissions for the space, they won't be able to see the page"
  (see the support links above, verified 2026-07-06). The content restrictions API
  returns ONLY restrictions applied directly to the queried content, never inherited
  ones (see the KB links), so this connector reconstructs the full conjunction itself.

  AccessControl is a pure UNION model (a viewer is anyone in allow_users OR
  allow_groups OR public, minus deny), so it structurally cannot express an AND. The
  connector therefore MATERIALIZES the conjunction to concrete principals at import
  time, and always maps to the NARROWER side, never the broader one:
    - It walks the page's parentId chain (v2 pages carry only the immediate parentId)
      and reads each ancestor's read restriction directly from GET
      /wiki/rest/api/content/{id}/restriction/byOperation/read (the one v1 content
      endpoint that accepts a granular token, covered by read:content.restriction).
    - It reads the space View audience (the real principals holding the space "read"
      permission) from GET /wiki/api/v2/spaces/{id}/permissions.
    - The emitted audience is the per-axis intersection (allow_users and allow_groups
      separately) across EVERY restriction layer AND the space View audience. A
      restriction principal that does not also hold space View is DROPPED: it cannot
      actually view the page, so emitting it would over-share to the whole company
      (e.g. a page restricted to "all-employees" inside a space only "engineering" can
      view must resolve to engineering, never to all-employees). This intersection is
      a SUBSET of the true effective audience, so it never widens a grant (no leak);
      when layers or the space audience name different identifier axes it can
      under-share (a correctness gap, not a security one), which is the safe side.
    - Only when the page AND every ancestor are CONFIRMED unrestricted (the read
      restriction expansion is present and names no principals) does the audience
      equal the space View audience alone. User principals map to allow_users
      (accountId), group principals to allow_groups (groupId), and role principals
      (RBAC tenants) to a namespaced "space-role:{id}" group the authz layer must
      materialize; an unmaterialized role simply grants no one, so it under-shares
      rather than leaks.
  Fail-closed on an unconfirmable restriction: the byOperation/read endpoint answers
  200 with empty result sets for an unrestricted page, so "unrestricted" is a positive
  signal, not an absence. If any restriction, ancestor, or space-permission read cannot
  complete (a non-2xx), the connector cannot confirm the audience, so it emits the empty
  (grants-no-one) audience rather than falling back to the space-wide one. Under-sharing
  is a UX bug; over-sharing is a security bug.
  Restriction groups are keyed by groupId (the stable identifier; group name is
  mutable and privacy-restricted), falling back to name only if the payload omits the
  id, so a layer is never mistakenly treated as unrestricted. There is no deny concept
  in Confluence view restrictions.

Discovery (list_resources)
  Enumerates the spaces the credential can reach via GET /wiki/api/v2/spaces (cursor
  pagination through _links.next), for the space_key picker. Each Resource id is the
  space key; the numeric v2 id is carried in extra. Only reachable spaces are
  returned, so discovery is a read the credential already permits, never a scope
  escalation. Read scope: read:space:confluence.

REST versions
  Content is read through v2 (GET /wiki/api/v2/pages/{id} for body/space/parent, the
  parentId walk for ancestry, /wiki/api/v2/pages?status=trashed for deletions), as are
  discovery and the space View audience (/wiki/api/v2/spaces and
  /wiki/api/v2/spaces/{id}/permissions). This is not optional: an OAuth token that
  carries any granular scope makes Atlassian evaluate the v1 content endpoints in
  granular mode, where they answer 401 "scope does not match" regardless of scopes held
  (verified 2026-07-19 against a live token). Two v1 endpoints are kept because they
  still accept the granular token and have no v2 parity the connector needs: the CQL
  change feed (GET /wiki/rest/api/content/search) and the read-restriction read (GET
  /wiki/rest/api/content/{id}/restriction/byOperation/read). Atlassian is deprecating v1
  endpoints that have v2 equivalents
  (https://community.developer.atlassian.com/t/rfc-19-deprecation-of-confluence-cloud-rest-api-v1-endpoints/71752);
  migrate these two once v2 exposes a CQL/lastmodified search and a content-restriction
  read, and re-verify against the docs.

Note: connector.py is the authoritative implementation for this source. The sibling
manifest.yaml is not used; see its banner.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

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

# Read-only OAuth 2.0 (3LO) scopes for the in-app Connect flow (provider
# "atlassian"). Verified 2026-07-19 against the Confluence scope reference
# (https://developer.atlassian.com/cloud/confluence/scopes-for-oauth-2-3LO-and-forge-apps/)
# AND against a live token: with granular scopes present, Atlassian's gateway evaluates
# content reads in granular mode, so a classic read:confluence-content.all no longer
# "matches" GET /wiki/rest/api/content/{id} and it 401s with "scope does not match". The
# granular read:page:confluence is what authorizes page-content reads then. We request
# BOTH the classic and the granular content scope so the connector works against a
# classic-only app (read:confluence-content.all) and a granular app (read:page:confluence)
# alike; Atlassian grants whichever the app has enabled. Each scope maps to an endpoint:
#   search:confluence                     -> GET /wiki/rest/api/content/search (CQL change
#                                            feed). Distinct from the content-read scopes;
#                                            the search endpoint 401s without it.
#   read:page:confluence                  -> GET /wiki/api/v2/pages/{id} page-content and
#                                            trashed reads (granular). The connector reads
#                                            content only through v2, because the v1
#                                            content endpoint 401s under a granular token.
#   read:confluence-content.all           -> the classic equivalent, kept only so a
#                                            classic-only Atlassian app can still read content.
#   read:confluence-space.summary         -> space info.
#   read:content.restriction:confluence   -> a page's read restrictions (the ACL).
#   read:space:confluence                 -> GET /wiki/api/v2/spaces (space discovery).
#   read:space.permission:confluence      -> GET /wiki/api/v2/spaces/{id}/permissions,
#                                            the space-wide View audience the ACL intersects
#                                            with. read:space:confluence does NOT cover it.
#   offline_access                        -> a rotating refresh token.
_OAUTH_SCOPES: tuple[str, ...] = (
    "search:confluence",
    "read:page:confluence",
    "read:confluence-content.all",
    "read:confluence-space.summary",
    "read:content.restriction:confluence",
    "read:space:confluence",
    "read:space.permission:confluence",
    "offline_access",
)

# A cursor floor used only when a tombstone is emitted on a run that has no upsert
# checkpoint yet (an unusual "nothing modified but something was trashed" first run).
# It is a valid, low lastmodified bound: the next incremental run simply re-scans,
# which is idempotent, never a leak.
_EPOCH_CURSOR = "1970-01-01T00:00:00.000Z"


def _parse_when(value: str) -> datetime | None:
    """Parse a Confluence version.when ISO-8601 timestamp, or None if unparseable."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _cql_minute(when: datetime) -> str:
    """Truncate a timestamp to the minute for a CQL lastmodified bound.

    CQL lastmodified only has minute granularity, so the query floor is coarse (a
    safe over-fetch) and the connector filters precisely on the full timestamp.
    """
    return when.strftime("%Y-%m-%d %H:%M")


def _read_restriction_by_operation(data: dict[str, Any]) -> tuple[list[str], list[str]] | None:
    """Parse the GET .../restriction/byOperation/read response into (accountIds, groupIds).

    Returns None when the content carries no direct read restriction (the endpoint
    answers 200 with empty user and group result sets), which means the layer does not
    narrow the audience. Unlike the retired expand-based reader, presence is not in
    question here: a 200 is proof the restriction was read, and the caller fails closed
    only on a non-2xx (which it cannot interpret as unrestricted). Groups are keyed by
    the stable groupId, falling back to name only when id is absent so a layer is never
    mistaken for unrestricted.

    The byOperation/read body nests the principals one level shallower than the old
    content expansion did: restrictions.user/group here, versus
    restrictions.read.restrictions.user/group on the content object.
    """
    restrictions = data.get("restrictions", {})
    users = [
        u["accountId"]
        for u in restrictions.get("user", {}).get("results", [])
        if u.get("accountId")
    ]
    groups = [
        g["id"] if g.get("id") else g["name"]
        for g in restrictions.get("group", {}).get("results", [])
        if g.get("id") or g.get("name")
    ]
    if not users and not groups:
        return None
    return users, groups


def _as_list(value: Any) -> list[str]:
    """Normalize a config value that may be a scalar or a list into a list of keys.

    The space field is multi-select (a list), but an older single-value config (or a
    hand-typed key) is a scalar, so both are accepted and empty entries are dropped."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return [str(value)]


def _intersect(sets: list[set[str]]) -> set[str]:
    if not sets:
        return set()
    result = set(sets[0])
    for other in sets[1:]:
        result &= other
    return result


class ConfluenceConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.instance_id = instance_id
        self._base = str(cfg.get("base_url", "")).rstrip("/")
        self._email = cfg.get("email", "")
        self._token = cfg.get("token", "")
        self._oauth_token = cfg.get("oauth_token", "")
        # One or more space keys to confine the instance to (a multi-select field, so a
        # list; a legacy scalar is still accepted). Empty means every reachable space.
        self._spaces = _as_list(cfg.get("space_key"))
        # Atlassian Cloud (OAuth) addresses the API through a gateway keyed by a cloud
        # id, not the site host. When only an OAuth token is sealed (no base_url), the
        # base is resolved from the token at run time (see _ensure_base). These two
        # optional fields disambiguate which site to use when the grant reaches several.
        self._cloud_id = str(cfg.get("cloud_id", "")).strip()
        self._site_url = str(cfg.get("site_url", "")).strip()
        # The site's human-openable web URL, filled once the cloud id is resolved, so a
        # document's source_url points at the site the user can actually open rather
        # than the api.atlassian.com gateway (which a browser cannot follow).
        self._site_web_url = ""
        # The space-wide view audience is constant per space within one sync run (the
        # runtime builds a fresh instance per sync), so it is safe to cache per key.
        self._space_acl_cache: dict[str, tuple[list[str], list[str]]] = {}
        self.meta = ConnectorMeta(
            name="confluence",
            auth=AuthScheme.api_token,
            capabilities=Capabilities(
                incremental=True,
                permission_sync=True,
                deletions=True,
                slim_listing=True,
                resource_discovery=True,
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
                    label="Spaces",
                    kind=FieldKind.LIST,
                    required=False,
                    placeholder="ENG",
                    resource_kind="space",
                    help="Limit ingestion to one or more spaces, or leave blank for all pages.",
                ),
            ),
        )

    def validate(self, config: dict[str, Any]) -> ValidationResult:
        # Auth is satisfied either by an OAuth token or by an email plus API token.
        has_oauth = bool(config.get("oauth_token"))
        has_basic = bool(config.get("email")) and bool(config.get("token"))
        if not has_oauth and not has_basic:
            return ValidationResult(
                ok=False,
                error="missing auth: connect with Atlassian, or set email and token",
            )
        # A manual instance (Server/DC, or Cloud basic auth) must give its base_url; an
        # OAuth (Atlassian Cloud) instance resolves its API base from the token at run
        # time (_ensure_base), so requiring base_url up front would wrongly reject a
        # freshly connected Cloud instance that never pastes a URL.
        if not has_oauth and not config.get("base_url"):
            return ValidationResult(ok=False, error="missing: base_url")
        return ValidationResult(ok=True)

    async def _ensure_base(self) -> None:
        """Resolve the Atlassian Cloud API base for an OAuth credential, once.

        A manually configured instance sets base_url directly, so this is a no op. An
        OAuth (Atlassian Cloud) connect seals only a token: the API base
        (https://api.atlassian.com/ex/confluence/{cloud_id}) must be discovered from
        the token itself. GET /oauth/token/accessible-resources lists the sites the
        grant can reach, each with a cloud id and a site URL. We pick the configured
        site (by cloud_id, else by a site_url substring) or the first, address the
        Confluence API through the gateway, and remember the site's web URL for
        building human-openable document links. Resolved once, then cached on _base.
        """
        if self._base or not self._oauth_token:
            return
        headers = {
            "Authorization": f"Bearer {self._oauth_token}",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            resp = await client.get("https://api.atlassian.com/oauth/token/accessible-resources")
            resp.raise_for_status()
            sites = resp.json()
        if not sites:
            raise RuntimeError(
                "the Atlassian grant can reach no sites; re-connect and grant a site"
            )
        chosen = self._pick_site(sites)
        self._base = f"https://api.atlassian.com/ex/confluence/{chosen['id']}"
        self._site_web_url = str(chosen.get("url", "")).rstrip("/")

    def _pick_site(self, sites: list[dict[str, Any]]) -> dict[str, Any]:
        """Choose which accessible Atlassian site to use.

        Prefers an exact cloud_id match, then a case-insensitive site_url substring
        match, then falls back to the first site the grant returned. Keeping the choice
        explicit means a multi-site grant is deterministic rather than order-dependent.
        """
        if self._cloud_id:
            for site in sites:
                if str(site.get("id")) == self._cloud_id:
                    return site
        if self._site_url:
            want = self._site_url.lower()
            for site in sites:
                if want in str(site.get("url", "")).lower():
                    return site
        return sites[0]

    def _client(self) -> httpx.AsyncClient:
        # Prefer the OAuth Bearer token when the Connect flow sealed one; otherwise
        # fall back to Basic auth with the manually pasted email plus API token.
        if self._oauth_token:
            auth = f"Bearer {self._oauth_token}"
        else:
            basic = base64.b64encode(f"{self._email}:{self._token}".encode()).decode()
            auth = f"Basic {basic}"
        return httpx.AsyncClient(
            base_url=self._base,
            headers={"Authorization": auth, "Accept": "application/json"},
            timeout=30.0,
        )

    # --- change feed ---
    def _changes_cql(self, since: datetime | None) -> str:
        clauses = ["type=page"]
        if self._spaces:
            quoted = ", ".join(f'"{s}"' for s in self._spaces)
            clauses.append(f"space in ({quoted})")
        if since is not None:
            clauses.append(f'lastmodified >= "{_cql_minute(since)}"')
        return " AND ".join(clauses) + " ORDER BY lastmodified ASC"

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        since = _parse_when(cursor) if cursor else None
        checkpoint = cursor or _EPOCH_CURSOR
        await self._ensure_base()
        async with self._client() as client:
            # Phase 1: incremental upserts by CQL lastmodified. The coarse minute
            # floor over-fetches; the precise version.when filter below drops any
            # page at or before the exact cursor so a replay leaks nothing.
            page_cursor: str | None = None
            while True:
                params: dict[str, Any] = {
                    "cql": self._changes_cql(since),
                    "limit": 100,
                    "expand": "version",
                }
                if page_cursor:
                    params["cursor"] = page_cursor
                resp = await client.get("/wiki/rest/api/content/search", params=params)
                resp.raise_for_status()
                data = resp.json()
                for page in data.get("results", []):
                    when = page.get("version", {}).get("when")
                    if since is not None and when is not None:
                        parsed = _parse_when(when)
                        if parsed is not None and parsed <= since:
                            continue
                    if when:
                        checkpoint = when
                    yield DocRef(source_doc_id=page["id"], instance_id=self.instance_id), checkpoint
                page_cursor = self._next_cursor(data)
                if not page_cursor:
                    break

            # Phase 2: deletions. Trashed pages are the removal signal, and CQL
            # cannot search trash, so poll the content list with status=trashed per
            # in-scope space. The tombstone carries the current upsert checkpoint so
            # the persisted cursor stays a valid lastmodified bound.
            async for ref in self._trashed(client):
                yield ref, checkpoint

    async def _trashed(self, client: httpx.AsyncClient) -> AsyncIterator[DocRef]:
        # v2 pages with status=trashed is the removal signal. The v1 content list rejects
        # a granular token (401 "scope does not match"), so this path is v2 only. When
        # spaces are configured the sweep is scoped to each by numeric space-id (the v2
        # filter); otherwise it lists every trashed page the credential can reach. A space
        # id it cannot resolve is skipped, not fatal.
        space_ids: list[str | None] = [None]
        if self._spaces:
            space_ids = [await self._space_id_for_key(client, key) for key in self._spaces]
        for space_id in space_ids:
            if self._spaces and space_id is None:
                continue
            params_base: dict[str, Any] = {"status": "trashed", "limit": 100}
            if space_id is not None:
                params_base["space-id"] = space_id
            cursor: str | None = None
            while True:
                params = dict(params_base)
                if cursor:
                    params["cursor"] = cursor
                resp = await client.get("/wiki/api/v2/pages", params=params)
                resp.raise_for_status()
                data = resp.json()
                for page in data.get("results", []):
                    if page.get("id"):
                        yield DocRef(
                            source_doc_id=str(page["id"]),
                            instance_id=self.instance_id,
                            deleted=True,
                        )
                cursor = self._next_cursor(data)
                if not cursor:
                    break

    async def _space_id_for_key(self, client: httpx.AsyncClient, key: str) -> str | None:
        """Resolve a space key (e.g. "ENG" or a "~personal" key) to its numeric v2 id.

        The v2 endpoints filter by numeric space-id, but the connector's space_key config
        and the picker both speak keys, so this bridges the two. Returns None when the key
        matches no reachable space, so the caller can skip rather than sweep every space.
        """
        resp = await client.get("/wiki/api/v2/spaces", params={"keys": key, "limit": 1})
        resp.raise_for_status()
        results = resp.json().get("results", [])
        first = results[0] if results else None
        return str(first["id"]) if first and first.get("id") is not None else None

    # --- documents + ACL ---
    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        # Read the page body through the v2 API. The v1 content endpoint rejects a
        # granular OAuth token (401 "scope does not match" once any granular scope is
        # present), while v2 /pages/{id} accepts it, so all content reads are v2.
        await self._ensure_base()
        async with self._client() as client:
            resp = await client.get(
                f"/wiki/api/v2/pages/{ref.source_doc_id}", params={"body-format": "storage"}
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
                source_url=self._page_url(page, ref.source_doc_id),
            )
            return raw, await self._acl_from_v2_page(client, page)

    async def list_acls(self, ref: DocRef) -> AccessControl:
        await self._ensure_base()
        async with self._client() as client:
            # A metadata-only v2 read: enough to learn the space and parent for the ACL
            # cascade, without pulling the body.
            resp = await client.get(f"/wiki/api/v2/pages/{ref.source_doc_id}")
            resp.raise_for_status()
            return await self._acl_from_v2_page(client, resp.json())

    def _page_url(self, page: dict[str, Any], content_id: str) -> str:
        """The human-openable page URL, from the v2 page's _links (base + webui).

        Falls back to the resolved site web URL (or the API base) with the legacy
        viewpage.action link when _links is absent, so a document always has a URL.
        """
        links = page.get("_links", {}) or {}
        base = str(links.get("base") or self._site_web_url or self._base).rstrip("/")
        webui = links.get("webui")
        if base and webui:
            return f"{base}{webui}"
        fallback = (self._site_web_url or self._base).rstrip("/")
        return f"{fallback}/wiki/pages/viewpage.action?pageId={content_id}"

    def _denied(self) -> AccessControl:
        """A fail-closed ACL that grants no one (not public, no allow principals).

        Emitted when a page's or an ancestor's read restriction cannot be read, so the
        content cannot be confirmed unrestricted. Under-sharing is a UX bug;
        over-sharing is a security bug, so an unconfirmable page grants no one.
        """
        return AccessControl(allow_users=[], allow_groups=[], principal_ns=self.instance_id)

    async def _acl_from_v2_page(
        self, client: httpx.AsyncClient, page: dict[str, Any]
    ) -> AccessControl:
        """Build the ACL for a v2 page, failing closed on any unreadable input.

        A restriction, ancestor, or space-permission read that cannot complete (a
        non-2xx) leaves the audience unconfirmable, and over-sharing is worse than
        under-sharing, so any such failure collapses the whole page to _denied rather
        than risk a leak. The HTTPStatusError catch is the single fail-closed seam;
        _build_acl below is the happy path.
        """
        try:
            return await self._build_acl(client, page)
        except httpx.HTTPStatusError:
            return self._denied()

    async def _build_acl(self, client: httpx.AsyncClient, page: dict[str, Any]) -> AccessControl:
        # Confluence view is a conjunction: satisfy the page's own read restriction AND
        # every ancestor's AND hold space View. A restriction only narrows within the
        # space, never grants beyond it, so a restricted page's effective audience is the
        # per-axis intersection of the restriction layers AND the space View audience,
        # materialized to concrete principals (AccessControl is a pure union and cannot
        # express the AND directly).
        content_id = str(page.get("id"))
        space_id = page.get("spaceId")

        # Reconstruct the restriction cascade: the page's own read restriction plus every
        # ancestor's. The byOperation/read endpoint never returns inherited restrictions,
        # so each ancestor is read directly. v2 gives ancestry as a parentId chain (the v2
        # ancestors endpoint needs a scope the granular token may lack), so it is walked.
        layers: list[tuple[list[str], list[str]]] = []
        own = await self._fetch_read_restriction(client, content_id)
        if own is not None:
            layers.append(own)
        for ancestor_id in await self._ancestor_ids(client, page.get("parentId")):
            inherited = await self._fetch_read_restriction(client, ancestor_id)
            if inherited is not None:
                layers.append(inherited)

        # The space View audience is a MANDATORY layer (a viewer must hold space View in
        # addition to satisfying every restriction), read for restricted and unrestricted
        # pages alike.
        space = await self._space_acl(client, space_id)

        if not layers:
            # Confirmed unrestricted on the whole chain: the audience equals the space
            # View audience alone.
            return space

        # Restricted: intersect the restriction-layer audience (per axis) with the space
        # View audience. A restriction principal that does not also hold space View is
        # dropped, so a page restricted to a broad group inside a narrow space never
        # over-shares to that whole group. This is a subset of the true audience, so it
        # never widens a grant.
        restriction_users = _intersect([set(u) for u, _ in layers])
        restriction_groups = _intersect([set(g) for _, g in layers])
        users = sorted(restriction_users & set(space.allow_users))
        groups = sorted(restriction_groups & set(space.allow_groups))
        return AccessControl(allow_users=users, allow_groups=groups, principal_ns=self.instance_id)

    async def _ancestor_ids(self, client: httpx.AsyncClient, first_parent_id: Any) -> list[str]:
        """Walk the parentId chain upward from a page, returning ancestor ids.

        Each v2 page carries only its immediate parentId, so the full chain is walked one
        page at a time (pages are typically shallow). A seen-set guards against a cycle in
        malformed data. A page read that fails propagates, which the ACL builder turns into
        a fail-closed audience.
        """
        ids: list[str] = []
        seen: set[str] = set()
        parent = first_parent_id
        while parent is not None:
            pid = str(parent)
            if pid in seen:
                break
            seen.add(pid)
            ids.append(pid)
            resp = await client.get(f"/wiki/api/v2/pages/{pid}")
            resp.raise_for_status()
            parent = resp.json().get("parentId")
        return ids

    async def _fetch_read_restriction(
        self, client: httpx.AsyncClient, content_id: str
    ) -> tuple[list[str], list[str]] | None:
        """Read a content item's direct read restriction via the v1 byOperation endpoint.

        This endpoint accepts the granular token (unlike the v1 content-read endpoint) and
        is covered by read:content.restriction:confluence. Returns (accountIds, groupIds)
        when restricted, or None when unrestricted (a 200 with empty result sets). A
        non-2xx propagates and the ACL builder fails closed, so an unreadable restriction
        is never mistaken for unrestricted.
        """
        resp = await client.get(f"/wiki/rest/api/content/{content_id}/restriction/byOperation/read")
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return _read_restriction_by_operation(result)

    async def _space_acl(self, client: httpx.AsyncClient, space_id: Any) -> AccessControl:
        """The space View audience for a numeric v2 space id, cached per id.

        A missing space id cannot yield a View audience, so it fails closed (grants no
        one). The View-space permission is exactly operation key=="read" AND
        targetType=="space"; a broad grant must rest on that positive signal.
        """
        if space_id is None:
            return self._denied()
        key = str(space_id)
        if key in self._space_acl_cache:
            cached_users, cached_groups = self._space_acl_cache[key]
            return AccessControl(
                allow_users=list(cached_users),
                allow_groups=list(cached_groups),
                principal_ns=self.instance_id,
            )

        users: list[str] = []
        groups: list[str] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            resp = await client.get(f"/wiki/api/v2/spaces/{key}/permissions", params=params)
            resp.raise_for_status()
            data = resp.json()
            for perm in data.get("results", []):
                operation = perm.get("operation", {})
                if operation.get("key") != "read":
                    continue
                if operation.get("targetType") != "space":
                    continue
                principal = perm.get("principal", {})
                principal_id = principal.get("id")
                principal_type = principal.get("type")
                if not principal_id:
                    continue
                if principal_type == "user":
                    users.append(principal_id)
                elif principal_type == "group":
                    groups.append(principal_id)
                elif principal_type == "role":
                    # RBAC role: the authz layer materializes its membership.
                    groups.append(f"space-role:{principal_id}")
            cursor = self._next_cursor(data)
            if not cursor:
                break

        users = sorted(set(users))
        groups = sorted(set(groups))
        self._space_acl_cache[key] = (users, groups)
        return AccessControl(
            allow_users=list(users), allow_groups=list(groups), principal_ns=self.instance_id
        )

    # --- discovery ---
    async def list_resources(
        self,
        kind: str | None = None,
        parent_id: str | None = None,
        cursor: str | None = None,
    ) -> ResourcePage:
        """List the Confluence spaces the configured credential can reach.

        The only discoverable kind is "space" (a flat level, so parent_id is always
        None). A None kind means the top level and lists spaces. Any other kind, or a
        non-None parent_id, has no children in this source and returns an empty page.

        Uses GET /wiki/api/v2/spaces with the source's own cursor pagination: the
        opaque `cursor` is passed straight through as the query parameter, and the
        next page's cursor is extracted from the relative _links.next URL the source
        returns (None when the listing is exhausted). Each Resource's id is the space
        key, which is exactly what the space_key config field the picker fills needs;
        the numeric v2 space id is carried in extra for callers that want it. This
        reuses the connector's existing auth, so discovery is a read the credential
        already permits, never a scope escalation.
        """
        if (kind is not None and kind != "space") or parent_id is not None:
            return ResourcePage(items=[], next_cursor=None)

        params: dict[str, Any] = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        await self._ensure_base()
        async with self._client() as client:
            resp = await client.get("/wiki/api/v2/spaces", params=params)
            resp.raise_for_status()
            data = resp.json()
        items = [
            Resource(
                id=space["key"],
                kind="space",
                label=space.get("name") or space["key"],
                parent_id=None,
                selectable=True,
                extra={"space_id": str(space["id"])} if space.get("id") is not None else {},
            )
            for space in data.get("results", [])
            if space.get("key")
        ]
        return ResourcePage(items=items, next_cursor=self._next_cursor(data))

    @staticmethod
    def _next_cursor(data: dict[str, Any]) -> str | None:
        """Extract the opaque cursor from a relative _links.next URL.

        Confluence returns the next page under _links.next as a relative URL like
        /wiki/api/v2/spaces?cursor=<opaque> (spaces, space permissions) or
        /rest/api/content/search?cql=...&cursor=<opaque> (content search). We return
        just the cursor value, which the caller passes back verbatim. A missing
        _links.next means the listing is exhausted, so we return None.
        """
        nxt = data.get("_links", {}).get("next")
        if not nxt:
            return None
        values = parse_qs(urlparse(nxt).query).get("cursor")
        return values[0] if values else None

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        return []
