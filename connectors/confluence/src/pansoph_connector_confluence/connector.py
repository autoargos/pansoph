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
  polls GET /wiki/rest/api/content?status=trashed&type=page&spaceKey=KEY&limit=100
  (start/limit pagination) for each in-scope space and yields a DocRef(deleted=True)
  per trashed page. Tombstones carry the current upsert checkpoint so the persisted
  cursor stays a valid lastmodified bound.

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
    - It expands ancestors on the page and fetches each ancestor's read restriction
      directly (GET content/{id}?expand=restrictions.read...).
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
  Fail-closed on an unconfirmable restriction: if the read-restriction expansion is
  ABSENT from a page or ancestor payload (a missing read:content.restriction scope, or
  a silently-empty 200 that raise_for_status cannot catch), the connector cannot
  confirm the content is unrestricted, so it emits the empty (grants-no-one) audience
  rather than falling back to the space-wide audience. Under-sharing is a UX bug;
  over-sharing is a security bug.
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
  Discovery and the space View audience use the v2 endpoints (GET
  /wiki/api/v2/spaces and /wiki/api/v2/spaces/{id}/permissions). Content list/fetch,
  the content restrictions expansions, and the trashed-content poll use the v1
  content endpoints (/wiki/rest/api/content*). This is deliberate: Atlassian is
  deprecating v1 endpoints that have v2 equivalents
  (https://community.developer.atlassian.com/t/rfc-19-deprecation-of-confluence-cloud-rest-api-v1-endpoints/71752),
  but the v1 content endpoints here are kept because v2 does not yet offer parity for
  the read-restriction expansions the ACL cascade needs, nor a CQL/trash equivalent,
  and Atlassian's feature-parity guarantee keeps them working. Migrate to
  /wiki/api/v2/pages once v2 exposes the restriction data; re-verify against the docs.

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
#   read:page:confluence                  -> GET /wiki/rest/api/content/{id} and
#                                            GET /wiki/api/v2/pages/{id} page-content reads
#                                            (granular). The classic scope below does not
#                                            satisfy these once granular scopes are present.
#   read:confluence-content.all           -> the classic equivalent of the page-content read,
#                                            for a classic-only app; also the trashed sweep.
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


def _restrictions_present(content: dict[str, Any]) -> bool:
    """True if the read-restriction expansion is present in `content`.

    Distinguishes a genuinely unrestricted page (the expansion is present and names
    no principals) from a payload where the expansion is ABSENT (a missing
    read:content.restriction scope, or a silently empty 200). Only the former may
    fall back to the space-wide audience; the latter must fail closed, because an
    absent expansion cannot confirm the content is unrestricted. When the expansion
    ran, Confluence always returns restrictions.read (with empty results if none),
    so the "read" key is the reliable presence signal.
    """
    return "read" in content.get("restrictions", {})


def _read_restriction(content: dict[str, Any]) -> tuple[list[str], list[str]] | None:
    """Return (accountIds, groupIds) directly restricted to view `content`, or None.

    None means no direct read restriction on this content. Groups are keyed by the
    stable groupId, falling back to name only if id is absent so the layer is never
    mistakenly counted as unrestricted.
    """
    read = content.get("restrictions", {}).get("read", {}).get("restrictions", {})
    users = [u["accountId"] for u in read.get("user", {}).get("results", []) if u.get("accountId")]
    groups = [
        g["id"] if g.get("id") else g["name"]
        for g in read.get("group", {}).get("results", [])
        if g.get("id") or g.get("name")
    ]
    if not users and not groups:
        return None
    return users, groups


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
        self._space = cfg.get("space_key", "")
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
                    label="Space key",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="ENG",
                    resource_kind="space",
                    help="Limit ingestion to one space. Leave blank to list all reachable pages.",
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
        if self._space:
            clauses.append(f'space="{self._space}"')
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
        space_keys = [self._space] if self._space else await self._all_space_keys(client)
        for key in space_keys:
            start = 0
            while True:
                params: dict[str, Any] = {
                    "type": "page",
                    "status": "trashed",
                    "spaceKey": key,
                    "limit": 100,
                    "start": start,
                }
                resp = await client.get("/wiki/rest/api/content", params=params)
                resp.raise_for_status()
                results = resp.json().get("results", [])
                for page in results:
                    if page.get("id"):
                        yield DocRef(
                            source_doc_id=page["id"],
                            instance_id=self.instance_id,
                            deleted=True,
                        )
                if len(results) < 100:
                    break
                start += 100

    async def _all_space_keys(self, client: httpx.AsyncClient) -> list[str]:
        keys: list[str] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            resp = await client.get("/wiki/api/v2/spaces", params=params)
            resp.raise_for_status()
            data = resp.json()
            keys.extend(s["key"] for s in data.get("results", []) if s.get("key"))
            cursor = self._next_cursor(data)
            if not cursor:
                break
        return keys

    # --- documents + ACL ---
    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        await self._ensure_base()
        async with self._client() as client:
            resp = await client.get(
                f"/wiki/rest/api/content/{ref.source_doc_id}",
                params={
                    "expand": (
                        "body.storage,ancestors,"
                        "restrictions.read.restrictions.user,"
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
                source_url=(
                    f"{self._site_web_url or self._base}"
                    f"/wiki/pages/viewpage.action?pageId={ref.source_doc_id}"
                ),
            )
            return raw, await self._acl_from_page(client, page)

    async def list_acls(self, ref: DocRef) -> AccessControl:
        await self._ensure_base()
        async with self._client() as client:
            resp = await client.get(
                f"/wiki/rest/api/content/{ref.source_doc_id}",
                params={
                    "expand": (
                        "ancestors,restrictions.read.restrictions.user,"
                        "restrictions.read.restrictions.group,space"
                    )
                },
            )
            resp.raise_for_status()
            return await self._acl_from_page(client, resp.json())

    def _denied(self) -> AccessControl:
        """A fail-closed ACL that grants no one (not public, no allow principals).

        Emitted when a page's or an ancestor's read-restriction expansion is absent,
        so the content cannot be confirmed unrestricted. Under-sharing is a UX bug;
        over-sharing is a security bug, so an unconfirmable page grants no one.
        """
        return AccessControl(allow_users=[], allow_groups=[], principal_ns=self.instance_id)

    async def _acl_from_page(
        self, client: httpx.AsyncClient, page: dict[str, Any]
    ) -> AccessControl:
        # Confluence view is a conjunction: satisfy the page's own read restriction
        # AND every ancestor's AND hold space View. A restriction can only narrow
        # within the space, never grant beyond it, so the effective audience of a
        # restricted page is the per-axis intersection of the restriction layers AND
        # the space View audience, materialized to concrete principals (AccessControl
        # is a pure union and cannot express the AND directly).

        # The page's own read-restriction expansion must be present. An absent
        # expansion cannot be confirmed unrestricted, so fail closed rather than fall
        # back to the space-wide audience.
        if not _restrictions_present(page):
            return self._denied()

        # Reconstruct the restriction cascade: the page's own read restriction plus
        # every ancestor's. The restrictions API never returns inherited restrictions,
        # so each ancestor is fetched directly.
        layers: list[tuple[list[str], list[str]]] = []
        own = _read_restriction(page)
        if own is not None:
            layers.append(own)
        for ancestor in page.get("ancestors", []):
            ancestor_id = ancestor.get("id")
            if not ancestor_id:
                continue
            ancestor_content = await self._fetch_content_restrictions(client, str(ancestor_id))
            if not _restrictions_present(ancestor_content):
                # Cannot confirm this ancestor is unrestricted: fail closed.
                return self._denied()
            inherited = _read_restriction(ancestor_content)
            if inherited is not None:
                layers.append(inherited)

        # The space View audience is a MANDATORY layer (a viewer must hold space View
        # in addition to satisfying every restriction), so it is read for restricted
        # and unrestricted pages alike.
        space = await self._space_acl(client, page.get("space", {}) or {})

        if not layers:
            # Confirmed unrestricted on the whole chain: the audience equals the space
            # View audience alone.
            return space

        # Restricted: intersect the restriction-layer audience (per axis) with the
        # space View audience. A restriction principal that does not also hold space
        # View is dropped, so a page restricted to a broad group inside a narrow space
        # never over-shares to that whole group. This is a subset of the true
        # audience, so it never widens a grant.
        restriction_users = _intersect([set(u) for u, _ in layers])
        restriction_groups = _intersect([set(g) for _, g in layers])
        users = sorted(restriction_users & set(space.allow_users))
        groups = sorted(restriction_groups & set(space.allow_groups))
        return AccessControl(allow_users=users, allow_groups=groups, principal_ns=self.instance_id)

    async def _fetch_content_restrictions(
        self, client: httpx.AsyncClient, content_id: str
    ) -> dict[str, Any]:
        """Fetch a content item's read-restriction expansion, returning the raw JSON.

        The caller checks _restrictions_present on the result to distinguish a
        confirmed-unrestricted ancestor from one whose expansion is absent (which must
        fail closed), then reads the named principals with _read_restriction.
        """
        resp = await client.get(
            f"/wiki/rest/api/content/{content_id}",
            params={
                "expand": (
                    "restrictions.read.restrictions.user,restrictions.read.restrictions.group"
                )
            },
        )
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result

    async def _space_acl(self, client: httpx.AsyncClient, space: dict[str, Any]) -> AccessControl:
        key = space.get("key") or self._space
        if key and key in self._space_acl_cache:
            cached_users, cached_groups = self._space_acl_cache[key]
            return AccessControl(
                allow_users=list(cached_users),
                allow_groups=list(cached_groups),
                principal_ns=self.instance_id,
            )

        space_id = space.get("id")
        users: list[str] = []
        groups: list[str] = []
        if space_id is not None:
            cursor: str | None = None
            while True:
                params: dict[str, Any] = {"limit": 100}
                if cursor:
                    params["cursor"] = cursor
                resp = await client.get(
                    f"/wiki/api/v2/spaces/{space_id}/permissions", params=params
                )
                resp.raise_for_status()
                data = resp.json()
                for perm in data.get("results", []):
                    operation = perm.get("operation", {})
                    # The View-space permission is exactly key=="read" AND
                    # targetType=="space". Require the explicit space target: a broad
                    # grant must rest on a positive signal, so a missing or non-space
                    # targetType is skipped (fail closed), never accepted defensively.
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
        if key:
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
