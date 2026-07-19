"""The jira connector (Jira Cloud).

Implemented against the Jira Cloud platform REST API v3. Endpoints, pagination, the
permission model, and the deletion signal were verified against the current official
docs on the dates noted below. Do not implement from memory (see pansoph CLAUDE.md
section 6).

  API intro:   https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/
  Search:      https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/#api-rest-api-3-search-jql-post
               (verified 2026-07-05) The old GET/POST /rest/api/3/search is REMOVED
               (410 Gone). Use POST /rest/api/3/search/jql with a JSON body
               {"jql": ..., "fields": [...], "maxResults": 100, "nextPageToken":
               <token>}. The response carries {"issues": [...], "nextPageToken": ...,
               "isLast": bool}. This is opaque token pagination: stop when isLast is
               true or nextPageToken is absent. There is no total count. Each issue
               object always carries "key" at the top level regardless of "fields".
  Issue read:  https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/#api-rest-api-3-issue-issueidorkey-get
               (verified 2026-07-05) GET /rest/api/3/issue/{key}?fields=... The
               "security" field (schema type securitylevel) is null for an
               unrestricted issue or {"id": "<levelId>", "name": ...} when an issue
               security level restricts the issue.
  ACL model:   verified 2026-07-05. Faithful, at the granularity the source exposes.
               (1) Browse audience: resolved from the project's permission scheme, NOT
                   a hardcoded role id. GET
                   /rest/api/3/project/{projectKeyOrId}/permissionscheme?expand=permissions
                   https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-project-permission-schemes/#api-rest-api-3-project-projectkeyorid-permissionscheme-get
                   Each grant has {"permission": "BROWSE_PROJECTS", "holder": {"type":
                   ..., "parameter": ...}}. Holder types mapped: "group" (parameter is
                   the group name) -> allow_groups; "user" (parameter is the accountId)
                   -> allow_users; "projectRole" (parameter is the role id) -> resolve
                   the role's actors; "applicationRole" / "anyone" / "loggedin"
                   ("any logged in user") -> a reserved, instance-namespaced membership
                   group (_LICENSED_USERS_GROUP), NEVER public. Verified 2026-07-05
                   against the permission-schemes holder reference
                   https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-permission-schemes/
                   and the permission-type reference
                   https://support.atlassian.com/jira-cloud-administration/docs/types-of-permissions-in-jira/
                   "any logged in user" is every principal holding an Atlassian PRODUCT
                   LICENSE on this site (any Jira/JSM/Confluence application access), a
                   set strictly NARROWER than "everyone in the pansoph tenant": a tenant
                   routinely holds accounts with no Atlassian license at all (pansoph-only
                   users), and mapping this holder to public would grant them read to an
                   issue they cannot browse in the source, an over-share (CLAUDE.md rule
                   1). This connector therefore NEVER emits public=True; it fails closed
                   to _LICENSED_USERS_GROUP, which the identity layer must materialize to
                   the licensed set. Until it is materialized the group has no members and
                   grants no one, which under-shares (safe) and never widens. Dynamic
                   holders that cannot resolve to fixed principals (reporter, assignee,
                   projectLead, group/user custom field, service desk customer) are
                   skipped, which under-shares and never widens.
                   Project role actors: GET
                   /rest/api/3/project/{projectKeyOrId}/role/{id}
                   https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-project-role-actors/#api-rest-api-3-project-projectidorkey-role-id-get
               (2) Issue security level: when fields.security is present (non-null, and
                   even an empty object) the issue is restricted to that level's members
                   ONLY, never the project browse audience. An empty or id-less security
                   object fails closed (grants no one) rather than inheriting the wider
                   project audience. Members: GET
                   /rest/api/3/issuesecurityschemes/level/member?levelId={id}
                   https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-security-level/
                   Paginated {"values": [{"holder": {...}}], "isLast": bool,
                   "startAt": int}. This endpoint requires the Administer Jira global
                   permission, which read credentials lack. If the members cannot be
                   resolved (any HTTP error), the connector FAILS CLOSED: it restricts
                   the issue (grants no one) rather than falling back to the project
                   audience, because over-sharing a security-restricted issue is a
                   security bug (CLAUDE.md non-negotiable rule 1).
               If the project's permission scheme itself cannot be read (the credential
               lacks read access to it), the browse audience also fails closed.
               Namespaced by the instance (principal_ns = instance_id).
  Deletions:   verified 2026-07-05. A poll of JQL cannot see a deleted issue: it simply
               disappears from results. Deletions are therefore detected by client-side
               reconciliation. list_changes encodes the set of issue keys seen this run
               into the durable cursor; on the next run any key present before but
               absent now is surfaced as a tombstone DocRef (deleted=True) so the
               runtime purges the document and its access tuples. The webhook path
               (on_webhook) additionally translates the real-time jira:issue_deleted
               event into a delete and jira:issue_created/updated into an upsert (which
               re-fetches and recomputes the ACL, so a newly applied security level
               propagates). Webhook payload shape verified against
               https://developer.atlassian.com/cloud/jira/platform/webhooks/
               (webhookEvent string, issue.key).
  Incremental: NOT claimed. The durable cursor carries the reconciliation key set, not
               an incremental high-water mark, so a full re-list runs every sync. This
               is deliberate: a full re-list is what lets reconciliation catch a removed
               or newly restricted issue and re-import every ACL, which serves the
               permission guarantee over sync efficiency. incremental=False is honest.
  Auth:        Two paths, both supported.
               (1) OAuth 2.0 (3LO): the in-app Connect flow (provider "atlassian")
                   mints a Bearer access token sealed into config key "oauth_token".
                   Verified 2026-07-05 against
                   https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/
                   and the scope reference
                   https://developer.atlassian.com/cloud/jira/platform/scopes-for-oauth-2-3LO-and-forge-apps/
                   Read scopes: read:jira-work (issue search, reads, and the project
                   permission scheme for a permitted project), read:jira-user (user and
                   role lookups), read:project-role:jira (project role actors), plus
                   offline_access for a rotating refresh token. NOTE: no read scope
                   grants issue security level membership (that needs Administer Jira
                   global), so the OAuth path resolves security-restricted issues by
                   failing closed.
               (2) Basic auth (manual paste): base64("{email}:{api_token}") sent as
                   "Authorization: Basic <b64>". The api_token is minted at
                   id.atlassian.com. A credential that is not a project or Jira admin
                   may also be unable to read the permission scheme or level members,
                   in which case the connector fails closed as above.
               The connector does not assume a scope; it maps whatever the credential
               can actually reach.
  OAuth base:  A 3LO token is NOT scoped to a site origin. After consent the caller
               resolves the cloudid via
               https://api.atlassian.com/oauth/token/accessible-resources and addresses
               the API at https://api.atlassian.com/ex/jira/{cloudid}. When an
               oauth_token is configured, base_url is that ex/jira/{cloudid} origin.
               This connector reads base_url as-is.
  Rate limit:  Atlassian uses a cost based rate limit with Retry-After. The runtime
               owns retries and backoff; a method only describes one page or document.
  ADF:         Issue descriptions are Atlassian Document Format, a nested JSON tree.
               _adf_text walks the tree and concatenates every node["text"].

Credential scope note: every configured instance carries its own credential and its
own ACL namespace (principal_ns = instance_id), so two instances never collide. See
pansoph CLAUDE.md section 6.

The runtime owns retries, backoff, and resumable sync, so a method only describes how
to talk to Jira for one page or one document.
"""

from __future__ import annotations

import base64
import json
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

# The Jira permission that governs who can see a project's issues. Resolved from the
# project's permission scheme (its holders are configurable per scheme), never from a
# fixed role id.
_BROWSE = "BROWSE_PROJECTS"

# The synthetic membership group standing in for Jira's "any logged in user" audience
# (holder types applicationRole / anyone / loggedin). This is NOT public. Verified
# 2026-07-05 against
# https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-permission-schemes/
# and https://support.atlassian.com/jira-cloud-administration/docs/types-of-permissions-in-jira/
# "any logged in user" means every principal holding an Atlassian product license on
# THIS site, which is strictly narrower than every principal in the pansoph tenant (a
# tenant routinely holds pansoph-only or otherwise unlicensed accounts with no Jira
# access). Mapping it to public would over-share to those unlicensed accounts, a
# security bug (CLAUDE.md non-negotiable rule 1: over-sharing is a security bug,
# under-sharing is only a UX bug). So it fails closed to this reserved,
# instance-namespaced membership group instead. The identity layer MUST materialize
# _LICENSED_USERS_GROUP to the concrete set of licensed Atlassian users on the site
# (from the user-management API); until it does, the group has no members and grants no
# one, which under-shares (safe) and never widens. The reserved double-underscore prefix
# mirrors the authz layer's convention (compare __pansoph_everyone) and does not collide
# with a real Jira group holder, which always carries its verbatim group name.
_LICENSED_USERS_GROUP = "__jira_licensed_users"

# Read-only OAuth 2.0 (3LO) scopes for the in-app Connect flow (provider "atlassian").
# Verified 2026-07-05 against the Jira scope reference. Classic read:jira-work covers
# issue search, issue reads, and the project permission scheme of a permitted project;
# read:jira-user covers user and role lookups; read:project-role:jira covers the
# project-role actors the ACL resolves; offline_access yields a rotating refresh token.
_OAUTH_SCOPES: tuple[str, ...] = (
    "read:jira-work",
    "read:jira-user",
    "read:project-role:jira",
    "offline_access",
)


def _adf_text(node: Any) -> str:
    """Flatten an Atlassian Document Format tree into plain text.

    ADF is a nested JSON document where text lives on leaf nodes of type "text". This
    walks the whole tree and concatenates every node["text"] it finds, in document
    order, separated by spaces.
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
        self._oauth_token: str = cfg.get("oauth_token", "")
        self._project_key: str = cfg.get("project_key", "")
        # Atlassian Cloud (OAuth) addresses the API through a gateway keyed by a cloud id,
        # not the site host. When only an OAuth token is sealed (no base_url), the base is
        # resolved from the token at run time (see _ensure_base). These optional fields
        # disambiguate which site to use when the grant reaches several.
        self._cloud_id: str = str(cfg.get("cloud_id", "")).strip()
        self._site_url: str = str(cfg.get("site_url", "")).strip()
        # The project browse audience is constant for a sync run, so it is resolved once
        # and reused. A connector instance lives for exactly one sync (the runtime builds
        # a fresh instance per sync), so caching on the instance never serves stale data
        # across runs. Security levels are cached by level id, since many issues share a
        # level and each members lookup is an extra rate-limited call.
        self._browse_cache: AccessControl | None = None
        self._level_cache: dict[str, AccessControl] = {}
        self.meta = ConnectorMeta(
            name="jira",
            auth=AuthScheme.api_token,
            capabilities=Capabilities(
                incremental=False, permission_sync=True, deletions=True, slim_listing=True
            ),
        )

    @classmethod
    def spec(cls) -> ConnectorSpec:
        return ConnectorSpec(
            name="jira",
            title="Jira",
            description="Ingest issues from a Jira Cloud project using an Atlassian API token.",
            fields=(
                ConfigField(
                    key="base_url",
                    label="Site URL",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="https://acme.atlassian.net",
                    help=(
                        "The Jira Cloud site origin, without a trailing path. Only needed "
                        "for the email plus API token option; a Connect-with-Atlassian "
                        "instance resolves its site from the token automatically."
                    ),
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
                    help="The Atlassian account email paired with the API token for Basic auth.",
                ),
                ConfigField(
                    key="api_token",
                    label="API token",
                    kind=FieldKind.SECRET,
                    required=False,
                    help=(
                        "An Atlassian API token minted at id.atlassian.com. "
                        "Sealed in the secret store. Not needed if you connected "
                        "with Atlassian above."
                    ),
                ),
                ConfigField(
                    key="project_key",
                    label="Project key",
                    kind=FieldKind.STRING,
                    placeholder="ENG",
                    help="The key of the project to ingest, for example ENG.",
                ),
            ),
        )

    # --- contract ---
    def validate(self, config: dict[str, Any]) -> ValidationResult:
        if not config.get("project_key"):
            return ValidationResult(ok=False, error="missing config: project_key")
        # Auth is satisfied either by an OAuth token or by an email plus API token.
        has_oauth = bool(config.get("oauth_token"))
        has_basic = bool(config.get("email")) and bool(config.get("api_token"))
        if not has_oauth and not has_basic:
            return ValidationResult(
                ok=False,
                error="missing auth: connect with Atlassian, or set email and api_token",
            )
        # A manual (basic-auth) instance must give its base_url; an OAuth (Atlassian Cloud)
        # instance resolves its API base from the token at run time (_ensure_base), so
        # requiring base_url up front would wrongly reject a freshly connected Cloud one.
        if not has_oauth and not config.get("base_url"):
            return ValidationResult(ok=False, error="missing config: base_url")
        return ValidationResult(ok=True)

    async def _ensure_base(self) -> None:
        """Resolve the Atlassian Cloud API base for an OAuth credential, once.

        A manually configured instance sets base_url directly, so this is a no op. An
        OAuth (Atlassian Cloud) connect seals only a token: the API base
        (https://api.atlassian.com/ex/jira/{cloud_id}) is discovered from the token via
        GET /oauth/token/accessible-resources, which lists the sites the grant can reach.
        The configured site is preferred (by cloud_id, else by a site_url substring), else
        the first. Resolved once, then cached on _base_url.
        """
        if self._base_url or not self._oauth_token:
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
        self._base_url = f"https://api.atlassian.com/ex/jira/{chosen['id']}"

    def _pick_site(self, sites: list[dict[str, Any]]) -> dict[str, Any]:
        """Choose which accessible Atlassian site to use: cloud_id, then site_url, then first."""
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

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        """Yield a ref for every issue in the project, plus deletion tombstones.

        A full re-list runs each sync (incremental is off). The durable `cursor` carries
        the set of issue keys seen on the previous run: any key present then but absent
        now is surfaced as a DocRef with deleted=True, so the runtime purges the removed
        (or now-invisible) issue and its access tuples. Every yielded tuple carries the
        same non-empty resume cursor encoding the keys seen on THIS run, so the next run
        can reconcile against it, and the conformance non-empty-cursor rule holds.
        """
        previous_keys = self._decode_cursor(cursor)
        current_keys: set[str] = set()
        jql = f"project = {self._project_key} ORDER BY updated ASC"
        await self._ensure_base()
        async with self._client() as client:
            page_token: str | None = None
            while True:
                body: dict[str, Any] = {"jql": jql, "fields": ["updated"], "maxResults": 100}
                if page_token:
                    body["nextPageToken"] = page_token
                resp = await client.post("/rest/api/3/search/jql", json=body)
                resp.raise_for_status()
                payload = resp.json()
                for issue in payload.get("issues", []):
                    key = issue.get("key", "")
                    if key:
                        current_keys.add(key)
                page_token = payload.get("nextPageToken")
                if payload.get("isLast", False) or not page_token:
                    break

        next_cursor = self._encode_cursor(current_keys)
        for key in sorted(current_keys):
            yield DocRef(source_doc_id=key, instance_id=self.instance_id), next_cursor
        # Reconciliation: a key seen before but gone now is a tombstone (deleted, or the
        # sync credential lost visibility, for example a new security level hid it).
        for key in sorted(previous_keys - current_keys):
            yield (
                DocRef(source_doc_id=key, instance_id=self.instance_id, deleted=True),
                next_cursor,
            )

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        await self._ensure_base()
        async with self._client() as client:
            resp = await client.get(
                f"/rest/api/3/issue/{ref.source_doc_id}",
                params={"fields": "summary,description,security"},
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
            return raw, await self._acl_for_issue(client, fields.get("security"))

    async def list_acls(self, ref: DocRef) -> AccessControl:
        # The ACL depends on the issue's own security level, so read just that field for
        # this issue rather than assuming the project audience.
        await self._ensure_base()
        async with self._client() as client:
            resp = await client.get(
                f"/rest/api/3/issue/{ref.source_doc_id}",
                params={"fields": "security"},
            )
            resp.raise_for_status()
            security = resp.json().get("fields", {}).get("security")
            return await self._acl_for_issue(client, security)

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        # Jira Cloud webhooks carry the event name in webhookEvent and the issue key in
        # issue.key. A deletion becomes a tombstone; a create or update becomes an upsert
        # so the ACL is recomputed (a newly applied security level then propagates).
        name = event.get("webhookEvent") or ""
        key = (event.get("issue") or {}).get("key") or ""
        if not key:
            return []
        if name == "jira:issue_deleted":
            return [ChangeEvent(kind="delete", source_doc_id=key, instance_id=self.instance_id)]
        if name in ("jira:issue_created", "jira:issue_updated"):
            return [ChangeEvent(kind="upsert", source_doc_id=key, instance_id=self.instance_id)]
        return []

    # --- helpers ---
    def _client(self) -> httpx.AsyncClient:
        # Prefer the OAuth Bearer token when the Connect flow sealed one; otherwise fall
        # back to Basic auth with the manually pasted email plus API token.
        if self._oauth_token:
            auth = f"Bearer {self._oauth_token}"
        else:
            basic = base64.b64encode(f"{self._email}:{self._api_token}".encode()).decode("ascii")
            auth = f"Basic {basic}"
        headers = {"Authorization": auth, "Accept": "application/json"}
        return httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=30.0)

    async def _acl_for_issue(
        self, client: httpx.AsyncClient, security: dict[str, Any] | None
    ) -> AccessControl:
        """Resolve one issue's ACL from its security field, else the project audience.

        A PRESENT security field (any non-null value, including an empty object) means an
        issue security level restricts the issue to that level's members only, never the
        wider project browse audience. Only a null/absent security field means the issue
        inherits the project's browse audience. An empty or id-less security object is a
        restriction we cannot name, so it fails closed (grants no one) rather than
        inheriting the broader browse audience (CLAUDE.md rule 1: over-sharing a
        restricted issue is a security bug).
        """
        if security is not None:
            level_id = str(security.get("id") or "")
            if not level_id:
                # Restricted by a level we cannot name (including an empty {}): fail
                # closed rather than widen to the project browse audience.
                return AccessControl(principal_ns=self.instance_id)
            return await self._security_acl(client, level_id)
        return await self._browse_acl(client)

    async def _browse_acl(self, client: httpx.AsyncClient) -> AccessControl:
        """Resolve the project's BROWSE_PROJECTS audience from its permission scheme."""
        if self._browse_cache is not None:
            return self._browse_cache
        try:
            resp = await client.get(
                f"/rest/api/3/project/{self._project_key}/permissionscheme",
                params={"expand": "permissions"},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            # The credential cannot read the project's permission scheme. Fail closed:
            # grant no one rather than guess a broad audience (CLAUDE.md rule 1).
            self._browse_cache = AccessControl(principal_ns=self.instance_id)
            return self._browse_cache
        users: set[str] = set()
        groups: set[str] = set()
        for grant in resp.json().get("permissions", []):
            if grant.get("permission") != _BROWSE:
                continue
            u, g = await self._map_holder(client, grant.get("holder") or {})
            users |= u
            groups |= g
        # public is never set: the connector cannot widen a holder to the whole tenant.
        self._browse_cache = AccessControl(
            public=False,
            allow_groups=sorted(groups),
            allow_users=sorted(users),
            principal_ns=self.instance_id,
        )
        return self._browse_cache

    async def _security_acl(self, client: httpx.AsyncClient, level_id: str) -> AccessControl:
        """Resolve the members of an issue security level to an ACL, else fail closed.

        The members endpoint needs the Administer Jira global permission, which read
        credentials lack. On any HTTP error the issue is restricted (granted to no one)
        rather than widened to the project audience, because over-sharing a
        security-restricted issue is a security bug (CLAUDE.md rule 1).
        """
        if level_id in self._level_cache:
            return self._level_cache[level_id]
        users: set[str] = set()
        groups: set[str] = set()
        try:
            start_at = 0
            while True:
                resp = await client.get(
                    "/rest/api/3/issuesecurityschemes/level/member",
                    params={"levelId": level_id, "startAt": start_at, "maxResults": 100},
                )
                resp.raise_for_status()
                data = resp.json()
                values = data.get("values", [])
                for member in values:
                    u, g = await self._map_holder(client, member.get("holder") or {})
                    users |= u
                    groups |= g
                if data.get("isLast", True) or not values:
                    break
                start_at += len(values)
        except httpx.HTTPStatusError:
            acl = AccessControl(principal_ns=self.instance_id)
            self._level_cache[level_id] = acl
            return acl
        # public is never set: the connector cannot widen a holder to the whole tenant.
        acl = AccessControl(
            public=False,
            allow_groups=sorted(groups),
            allow_users=sorted(users),
            principal_ns=self.instance_id,
        )
        self._level_cache[level_id] = acl
        return acl

    async def _map_holder(
        self, client: httpx.AsyncClient, holder: dict[str, Any]
    ) -> tuple[set[str], set[str]]:
        """Map one permission-scheme grant or security-level holder to (users, groups).

        Returns the principals a holder resolves to. This never returns a public flag:
        the connector is structurally incapable of emitting public=True, so no holder can
        be widened to the whole tenant. An unmapped holder type (a dynamic one like
        reporter, assignee, project lead, or a custom field that cannot resolve to fixed
        principals) yields nothing, which under-shares and never widens.
        """
        holder_type = holder.get("type")
        if holder_type == "user":
            account_id = (
                holder.get("parameter")
                or holder.get("value")
                or (holder.get("user") or {}).get("accountId")
            )
            return ({account_id} if account_id else set()), set()
        if holder_type == "group":
            name = holder.get("parameter") or (holder.get("group") or {}).get("name")
            return set(), ({name} if name else set())
        if holder_type == "projectRole":
            role_id = holder.get("parameter") or (holder.get("projectRole") or {}).get("id")
            if not role_id:
                return set(), set()
            return await self._role_actors(client, str(role_id))
        if holder_type in ("applicationRole", "anyone", "loggedin"):
            # "Any logged in user" = any principal with an Atlassian product license on
            # this site, strictly narrower than the whole pansoph tenant. Fail closed to
            # the reserved membership group the identity layer materializes, NEVER public.
            # See _LICENSED_USERS_GROUP for the doc-grounded reasoning.
            return set(), {_LICENSED_USERS_GROUP}
        return set(), set()

    async def _role_actors(
        self, client: httpx.AsyncClient, role_id: str
    ) -> tuple[set[str], set[str]]:
        """Resolve a project role's actors to (user accountIds, group names)."""
        resp = await client.get(f"/rest/api/3/project/{self._project_key}/role/{role_id}")
        resp.raise_for_status()
        users: set[str] = set()
        groups: set[str] = set()
        for actor in resp.json().get("actors", []):
            user = actor.get("actorUser")
            if user and user.get("accountId"):
                users.add(user["accountId"])
            group = actor.get("actorGroup")
            if group and group.get("name"):
                groups.add(group["name"])
        return users, groups

    @staticmethod
    def _encode_cursor(keys: set[str]) -> str:
        """Encode the issue keys seen this run into an opaque, non-empty resume cursor.

        A base64 JSON sorted list, persisted and replayed verbatim by the runtime. It is
        always non-empty (an empty run still encodes []), so the conformance contract's
        non-empty-cursor requirement holds.
        """
        blob = json.dumps(sorted(keys), separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(blob).decode("ascii")

    @staticmethod
    def _decode_cursor(cursor: str | None) -> set[str]:
        """Decode a resume cursor into the set of issue keys seen on the previous run.

        A None or unparseable cursor means no prior state (a first run), so no deletion
        is inferred. A malformed cursor is treated as empty rather than raised on: a bad
        prior state must not fail the sync, and empty is the safe direction (it
        under-reports deletions, never fabricates one).
        """
        if not cursor:
            return set()
        try:
            blob = base64.urlsafe_b64decode(cursor.encode("ascii"))
            data = json.loads(blob.decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            return set()
        if isinstance(data, list):
            return {str(k) for k in data}
        return set()
