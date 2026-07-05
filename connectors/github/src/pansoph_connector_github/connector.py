"""The github connector.

Implemented against the GitHub REST API. Verified against the official docs:

  API docs:    https://docs.github.com/en/rest/repos/contents (get content)
               https://docs.github.com/en/rest/commits/commits (list commits with
                 `since`; a single commit's files[].status is one of added, removed,
                 modified, renamed, copied, changed, unchanged; `since` is
                 ISO 8601 YYYY-MM-DDTHH:MM:SSZ and filters commits after that time)
               https://docs.github.com/en/rest/git/trees (recursive tree)
               https://docs.github.com/en/rest/collaborators/collaborators
                 (GET /repos/{owner}/{repo}/collaborators with affiliation=all returns
                 outside collaborators, org members with direct access, org members
                 with access via team membership, org members with default org
                 permissions, and org owners; each item carries login and a
                 permissions object plus role_name)
               https://docs.github.com/en/rest/teams/teams
               https://docs.github.com/en/rest/repos/repos (repository `visibility` is
                 one of public, private, internal; an internal repo carries
                 private=true and visibility="internal")
  Visibility:  https://docs.github.com/en/repositories/creating-and-managing-repositories/about-repositories
               https://github.blog/news-insights/product-news/internal-repositories-are-now-generally-available-for-github-enterprise/
                 (an internal repo, available on GitHub Enterprise Cloud/Server, is
                 readable by every member of the enterprise, i.e. any member of any
                 organization in the enterprise account; outside collaborators and
                 non-members cannot read it)
  Discovery:   https://docs.github.com/en/rest/orgs/orgs#list-organizations-for-the-authenticated-user
                 (GET /user/orgs; org objects carry `login`)
               https://docs.github.com/en/rest/repos/repos#list-organization-repositories
                 (GET /orgs/{org}/repos; repo objects carry `name`, `full_name`, `private`)
               https://docs.github.com/en/rest/repos/repos#list-repositories-for-the-authenticated-user
                 (GET /user/repos; the credential's own repos, listed when no org is given)
  API version: X-GitHub-Api-Version 2026-03-10
  Auth:        bearer token. The token may be a personal access token, an org or
               GitHub App installation token, or any RBAC scoped credential. The
               connector does not assume a scope; it maps whatever the credential
               can reach. Needs read access to repository contents and metadata,
               and to members for ACL sync.
  Pagination:  per_page up to 100, follow the Link header rel="next".
  Incremental: list commits with `since`, then read each commit's changed files. A
               file whose status is "removed" in a commit is surfaced as a deleted
               DocRef (a tombstone), so a poll-based sync purges removed files.
  Rate limit:  5000 requests per hour authenticated; surfaced, not hidden.
  ACL model:   the map fails closed. Only an EXPLICIT public signal (visibility ==
               "public") maps to public; a missing or ambiguous visibility/private is
               NEVER treated as public, since base_url is configurable and a trimmed
               body on the ACL path could otherwise publish a private repo. An internal
               repo (GitHub Enterprise) maps to a synthetic enterprise-members group,
               because every enterprise member can read it, and NOT to public. A private
               repo, or any body without the explicit public/internal signal, maps to
               the collaborators (users) and teams (groups) that the credential can see,
               namespaced by the instance. Because access is RBAC controlled, the ACL
               reflects the credential's actual reach, not an assumption. Over-sharing
               is a security bug; under-sharing is only a UX bug (pansoph CLAUDE.md
               rule 1), so every ambiguous case resolves to the narrower audience.
  Verified on: 2026-07-05.

Credential scope note: every configured instance carries its own credential and
its own ACL namespace (principal_ns = instance_id). One instance may be a personal
token over one user's repos, another an org installation over many, another a team
scoped credential. They never collide, and each maps ACLs at the granularity its
credential grants. See pansoph CLAUDE.md section 6.

The runtime owns retries, backoff, and resumable sync, so a method only describes
how to talk to GitHub for one page or one document.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
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

_API_VERSION = "2026-03-10"
_TEXTUAL = {
    ".md",
    ".markdown",
    ".rst",
    ".txt",
    ".adoc",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".rb",
    ".c",
    ".h",
    ".cpp",
    ".cs",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".html",
    ".css",
    ".sql",
    ".sh",
    ".csv",
}


def _is_textual(path: str) -> bool:
    dot = path.rfind(".")
    return dot != -1 and path[dot:].lower() in _TEXTUAL


class GithubConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        self.instance_id = instance_id
        cfg = config or {}
        self._token: str = cfg.get("token", "")
        self._owner: str = cfg.get("owner", "")
        self._repo: str = cfg.get("repo", "")
        self._branch: str | None = cfg.get("branch")
        self._base_url: str = cfg.get("base_url", "https://api.github.com")
        # Bound ingestion of large/popular repos: only files under `path`, capped at
        # `max_files`, so a token's rate limit is respected.
        self._path: str = cfg.get("path", "")
        self._max_files: int = int(cfg.get("max_files", 300))
        # Per-sync caches. The repo metadata and the repo-level ACL are the same for every
        # file, so fetch them once instead of once per file. Fetching the ACL per file was
        # doubling API calls and exhausting the unauthenticated rate limit mid-sync.
        self._repo_info_cache: dict[str, Any] | None = None
        self._acl_cache: AccessControl | None = None
        # The authenticated user's login, resolved once and reused by resource discovery
        # to name and route the synthetic "your repositories" owner.
        self._user_login_cache: str | None = None
        self.meta = ConnectorMeta(
            name="github",
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
            name="github",
            title="GitHub",
            description="Ingest textual files from a GitHub repository over the REST API.",
            fields=(
                ConfigField(
                    key="owner",
                    label="Owner",
                    kind=FieldKind.STRING,
                    placeholder="octocat",
                    help="The user or organization that owns the repository.",
                    # Picker binds to org resources (parent none). Free-text fallback
                    # stands for a personal owner or an air-gapped PAT user.
                    resource_kind="org",
                ),
                ConfigField(
                    key="repo",
                    label="Repository",
                    kind=FieldKind.STRING,
                    placeholder="Hello-World",
                    help="The repository name, without the owner prefix.",
                    # Picker binds to repo resources (children of the chosen org). The
                    # repo resource id is `owner/repo`; the connector splits it back into
                    # owner and repo, so the picked value fills both fields faithfully.
                    resource_kind="repo",
                ),
                ConfigField(
                    key="token",
                    label="Access token",
                    kind=FieldKind.OAUTH,
                    required=False,
                    oauth_provider="github",
                    # Read-oriented default scopes. `repo` is the only scope that reads
                    # PRIVATE repository contents (GitHub has no read-only private-repo
                    # scope); read:org and read:user are read-only and back the
                    # collaborator/team ACL sync. Verified 2026-07-04 against
                    # https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps
                    oauth_scopes=("repo", "read:org", "read:user"),
                    help=(
                        "Connect with GitHub to mint a bearer token, or paste one by hand "
                        "(a personal access token, or an org or GitHub App installation "
                        "token). Optional for public repositories, required for private "
                        "ones. Sealed in the secret store under the same key either way."
                    ),
                ),
                ConfigField(
                    key="branch",
                    label="Branch",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="main",
                    help="Branch to read. Defaults to the repository's default branch.",
                ),
                ConfigField(
                    key="path",
                    label="Path prefix",
                    kind=FieldKind.STRING,
                    required=False,
                    help="Only ingest files under this path prefix. Empty ingests the whole repo.",
                ),
                ConfigField(
                    key="max_files",
                    label="Max files",
                    kind=FieldKind.INT,
                    required=False,
                    placeholder="300",
                    help="Cap on files ingested per sync, to respect the token's rate limit.",
                ),
                ConfigField(
                    key="base_url",
                    label="API base URL",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="https://api.github.com",
                    help="Override only for GitHub Enterprise or a self-managed gateway.",
                ),
            ),
        )

    # --- contract ---
    def validate(self, config: dict[str, Any]) -> ValidationResult:
        # token is optional: public repositories are readable unauthenticated (at a
        # lower rate limit). A private repo needs a token with the right scope.
        missing = [k for k in ("owner", "repo") if not config.get(k)]
        if missing:
            return ValidationResult(ok=False, error=f"missing config: {', '.join(missing)}")
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        async with self._client() as client:
            branch = self._branch or (await self._repo_info(client))["default_branch"]
            next_cursor = await self._latest_commit_date(client) or ""
            count = 0
            if cursor is None:
                # First full pass: the current tree. Every path is an upsert; a full
                # crawl has no removals to report.
                async for path in self._all_paths(client, branch):
                    if self._path and not path.startswith(self._path):
                        continue
                    yield self._ref(path), next_cursor
                    count += 1
                    if count >= self._max_files:
                        break
            else:
                # Incremental pass: each changed file carries its commit status. A
                # "removed" file becomes a deleted DocRef (a tombstone) so the runtime
                # purges the document and its access tuples; everything else is an
                # upsert. Both count against max_files.
                async for path, removed in self._changed_paths(client, cursor):
                    if self._path and not path.startswith(self._path):
                        continue
                    yield self._ref(path, deleted=removed), next_cursor
                    count += 1
                    if count >= self._max_files:
                        break

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        async with self._client() as client:
            params = {"ref": self._branch} if self._branch else None
            resp = await client.get(
                f"/repos/{self._owner}/{self._repo}/contents/{ref.source_doc_id}",
                params=params,
            )
            resp.raise_for_status()
            payload = resp.json()
            content = payload.get("content", "")
            encoding = payload.get("encoding", "base64")
            if encoding == "base64":
                text = base64.b64decode(content).decode("utf-8", "replace")
            else:
                text = content
            raw = RawDoc(
                source_doc_id=ref.source_doc_id,
                instance_id=self.instance_id,
                mime="text/plain",
                text=text,
                title=ref.source_doc_id.rsplit("/", 1)[-1],
                source_url=payload.get("html_url"),
            )
            return raw, await self._acl(client)

    async def list_acls(self, ref: DocRef) -> AccessControl:
        async with self._client() as client:
            return await self._acl(client)

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        # GitHub push webhooks carry added, modified, and removed paths per commit.
        changes: list[ChangeEvent] = []
        for commit in event.get("commits", []):
            for path in [*commit.get("added", []), *commit.get("modified", [])]:
                changes.append(
                    ChangeEvent(kind="upsert", source_doc_id=path, instance_id=self.instance_id)
                )
            for path in commit.get("removed", []):
                changes.append(
                    ChangeEvent(kind="delete", source_doc_id=path, instance_id=self.instance_id)
                )
        return changes

    async def list_resources(
        self,
        kind: str | None = None,
        parent_id: str | None = None,
        cursor: str | None = None,
    ) -> ResourcePage:
        """Enumerate what the credential can reach, for the config picker.

        Two levels, hierarchical by (kind, parent_id), each a valid `owner`/`repo`:

        - Top level (kind None or "org", parent None): the organizations the
          credential belongs to (GET /user/orgs), each a selectable `owner`, plus a
          synthetic "user" org for the authenticated account itself so a personal
          credential with no orgs can still drill into its own repos.
        - Repos (kind "repo", parent = an org login): the repositories the credential
          can see under that owner. GET /orgs/{org}/repos for a real org, and
          GET /user/repos for the synthetic user node (the /orgs endpoint 404s for a
          user login). Each repo id is `owner/repo` (its full_name), the opaque value
          the `repo` field's picker resolves to; the connector splits it back into
          owner and repo at sync time.

        Listing is only ever what the credential already reaches, so the resource tree
        equals the ACL reach and discovery never escalates scope. Pagination is
        GitHub's own: the opaque cursor is the next page number carried in the Link
        header, and next_cursor is None once the Link header has no rel="next".
        """
        async with self._client() as client:
            if kind == "repo":
                if not parent_id:
                    return ResourcePage()
                return await self._list_repos(client, parent_id, cursor)
            # kind None or "org": the top level.
            return await self._list_orgs(client, cursor)

    # --- helpers ---
    async def _list_orgs(self, client: httpx.AsyncClient, cursor: str | None) -> ResourcePage:
        # The authenticated user itself is a synthetic top-level owner so a personal
        # credential (which may belong to no org) can still reach its own repos. It is
        # only surfaced on the first page, so it is not duplicated across pages.
        items: list[Resource] = []
        if cursor is None:
            user = await self._authenticated_user(client)
            if user:
                items.append(
                    Resource(
                        id=user,
                        kind="org",
                        label=f"{user} (your repositories)",
                        extra={"scope": "user"},
                    )
                )
        resp = await client.get("/user/orgs", params=self._page_params(cursor, {"per_page": 100}))
        resp.raise_for_status()
        for org in resp.json():
            login = org.get("login")
            if login:
                items.append(Resource(id=login, kind="org", label=login))
        return ResourcePage(items=items, next_cursor=self._next_page(resp))

    async def _list_repos(
        self, client: httpx.AsyncClient, owner: str, cursor: str | None
    ) -> ResourcePage:
        # A real org lists via /orgs/{org}/repos; the synthetic user owner (its login
        # equals the authenticated user) lists via /user/repos, since /orgs/{login}
        # 404s for a user. Fall back to /user/repos when the org endpoint 404s, so a
        # personal owner passed straight through still resolves.
        user = await self._authenticated_user(client)
        if owner == user:
            resp = await client.get(
                "/user/repos",
                params=self._page_params(cursor, {"affiliation": "owner", "per_page": 100}),
            )
        else:
            resp = await client.get(
                f"/orgs/{owner}/repos",
                params=self._page_params(cursor, {"type": "all", "per_page": 100}),
            )
            if resp.status_code == 404:
                resp = await client.get(
                    "/user/repos", params=self._page_params(cursor, {"per_page": 100})
                )
        resp.raise_for_status()
        items: list[Resource] = []
        for repo in resp.json():
            full = repo.get("full_name") or f"{owner}/{repo.get('name', '')}"
            name = repo.get("name") or full
            if repo.get("name") or repo.get("full_name"):
                items.append(
                    Resource(
                        id=full,
                        kind="repo",
                        label=name,
                        parent_id=owner,
                        extra={"private": str(bool(repo.get("private", False))).lower()},
                    )
                )
        return ResourcePage(items=items, next_cursor=self._next_page(resp))

    async def _authenticated_user(self, client: httpx.AsyncClient) -> str | None:
        # Cached for the life of the connector: the login of the token holder, used to
        # name the synthetic user owner and to route its repo listing to /user/repos.
        # No token means an unauthenticated credential, which reaches no user or orgs.
        if not self._token:
            return None
        if self._user_login_cache is None:
            resp = await client.get("/user")
            resp.raise_for_status()
            self._user_login_cache = resp.json().get("login") or ""
        return self._user_login_cache or None

    @staticmethod
    def _page_params(cursor: str | None, base: dict[str, Any]) -> dict[str, Any]:
        # The opaque cursor is GitHub's own page number; None is the first page.
        params = dict(base)
        if cursor:
            params["page"] = cursor
        return params

    def _next_page(self, resp: httpx.Response) -> str | None:
        # Derive the next page number from the Link header rel="next" so the cursor
        # stays the source's own pagination model, not a runtime reconstruction.
        next_url = self._next_link(resp)
        if not next_url:
            return None
        query = urlparse(next_url).query
        page = parse_qs(query).get("page")
        return page[0] if page else None

    def _client(self) -> httpx.AsyncClient:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=30.0)

    def _ref(self, path: str, *, deleted: bool = False) -> DocRef:
        return DocRef(source_doc_id=path, instance_id=self.instance_id, deleted=deleted)

    def _enterprise_members_group(self) -> str:
        # The synthetic group that stands for "every member of the enterprise", the true
        # audience of an internal repo. It is anchored on the owning org so it does not
        # collide with a real team slug (teams are per-org slugs, never prefixed like
        # this), and the instance namespace (principal_ns) already isolates it from
        # other instances. The runtime maps this group to the enterprise's membership.
        return f"enterprise:{self._owner}:members"

    async def _repo_info(self, client: httpx.AsyncClient) -> dict[str, Any]:
        # Cached for the life of the connector (one sync run), so listing and every fetch
        # share a single repo metadata call.
        if self._repo_info_cache is None:
            resp = await client.get(f"/repos/{self._owner}/{self._repo}")
            resp.raise_for_status()
            self._repo_info_cache = resp.json()
        return self._repo_info_cache

    async def _latest_commit_date(self, client: httpx.AsyncClient) -> str | None:
        resp = await client.get(
            f"/repos/{self._owner}/{self._repo}/commits", params={"per_page": 1}
        )
        resp.raise_for_status()
        commits = resp.json()
        if not commits:
            return None
        return commits[0]["commit"]["committer"]["date"]

    async def _all_paths(self, client: httpx.AsyncClient, branch: str) -> AsyncIterator[str]:
        resp = await client.get(
            f"/repos/{self._owner}/{self._repo}/git/trees/{branch}",
            params={"recursive": "1"},
        )
        resp.raise_for_status()
        for item in resp.json().get("tree", []):
            if item.get("type") == "blob" and _is_textual(item.get("path", "")):
                yield item["path"]

    async def _changed_paths(
        self, client: httpx.AsyncClient, since: str
    ) -> AsyncIterator[tuple[str, bool]]:
        """Yield (path, removed) for each textual file changed since `since`.

        `removed` is True when the file's most recent commit status in the window is
        "removed" (https://docs.github.com/en/rest/commits/commits), which the caller
        turns into a deleted DocRef tombstone. GitHub lists commits newest first, so
        the first status seen for a path is its latest state, and a path re-added after
        a deletion (or deleted after an edit) is reported by that latest state only.
        A "removed" is emitted for the exact document id (the file path) so the runtime
        purges precisely that document, per rule 1 (revocations must propagate).
        """
        seen: set[str] = set()
        url: str | None = f"/repos/{self._owner}/{self._repo}/commits"
        params: dict[str, Any] | None = {"since": since, "per_page": 100}
        while url:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            for entry in resp.json():
                detail = await client.get(
                    f"/repos/{self._owner}/{self._repo}/commits/{entry['sha']}"
                )
                detail.raise_for_status()
                for f in detail.json().get("files", []):
                    path = f.get("filename", "")
                    if not _is_textual(path) or path in seen:
                        continue
                    seen.add(path)
                    yield path, f.get("status") == "removed"
            url, params = self._next_link(resp), None

    @staticmethod
    def _next_link(resp: httpx.Response) -> str | None:
        link = resp.headers.get("link", "")
        for part in link.split(","):
            if 'rel="next"' in part:
                start = part.find("<") + 1
                end = part.find(">")
                if 0 < start < end:
                    return part[start:end]
        return None

    async def _acl(self, client: httpx.AsyncClient) -> AccessControl:
        # The ACL is repo level, so compute it once per sync and reuse it for every file.
        if self._acl_cache is not None:
            return self._acl_cache
        repo = await self._repo_info(client)
        ns = self.instance_id
        visibility = repo.get("visibility")
        # Fail closed on the ACL-decision path: only an EXPLICIT public signal makes a
        # repo world-readable. GitHub's GET /repos/{owner}/{repo} always returns the
        # required boolean `private` and a `visibility` of public, private, or internal
        # (https://docs.github.com/en/rest/repos/repos, verified 2026-07-05), but
        # `base_url` is configurable for GitHub Enterprise or a self-managed gateway, so
        # an untrusted intermediary could return a trimmed body on this path. We must
        # never infer public from an absence: a missing or ambiguous visibility/private
        # (for example a body that omits both) must NOT map to public. Deriving public
        # from `not repo.get("private", False)` would treat a MISSING `private` as
        # public and publish a genuinely private repo to the tenant-everyone group, an
        # over-share. Only visibility == "public" grants the public audience; every
        # other shape falls through to the narrower collaborators path below.
        # Over-sharing is a security bug; under-sharing a truly public repo (its
        # collaborators still see it) is only a UX bug. See pansoph CLAUDE.md rule 1.
        if visibility == "public":
            self._acl_cache = AccessControl(public=True, principal_ns=ns)
            return self._acl_cache
        # An internal repo (GitHub Enterprise) carries private=true and
        # visibility="internal", and is readable by every member of the enterprise,
        # not just its collaborators and teams. Mapping it to only collaborators would
        # over-deny entitled enterprise members. It is NOT public either, so map it to a
        # synthetic enterprise-members group. The runtime resolves that group's members
        # from the enterprise identity source; here the connector only names the correct
        # audience, namespaced by the instance so scopes never collide.
        if visibility == "internal":
            self._acl_cache = AccessControl(
                allow_groups=[self._enterprise_members_group()], principal_ns=ns
            )
            return self._acl_cache
        # A private repo, or any body that did not carry the explicit public/internal
        # signal above (the fail-closed default): collaborators (users) and teams
        # (groups) the credential sees. A partial credential under-shares, never over.
        users = [
            login
            async for login in self._paginate_field(
                client,
                f"/repos/{self._owner}/{self._repo}/collaborators",
                {"affiliation": "all", "per_page": 100},
                "login",
            )
        ]
        groups = [
            slug
            async for slug in self._paginate_field(
                client, f"/repos/{self._owner}/{self._repo}/teams", {"per_page": 100}, "slug"
            )
        ]
        self._acl_cache = AccessControl(allow_users=users, allow_groups=groups, principal_ns=ns)
        return self._acl_cache

    async def _paginate_field(
        self, client: httpx.AsyncClient, path: str, params: dict[str, Any], field: str
    ) -> AsyncIterator[str]:
        url: str | None = path
        page_params: dict[str, Any] | None = params
        while url:
            resp = await client.get(url, params=page_params)
            resp.raise_for_status()
            for item in resp.json():
                value = item.get(field)
                if value:
                    yield value
            url, page_params = self._next_link(resp), None
