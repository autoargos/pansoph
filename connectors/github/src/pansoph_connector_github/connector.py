"""The github connector.

Implemented against the GitHub REST API. Verified against the official docs:

  API docs:    https://docs.github.com/en/rest/repos/contents (get content)
               https://docs.github.com/en/rest/commits/commits (list commits, since)
               https://docs.github.com/en/rest/git/trees (recursive tree)
               https://docs.github.com/en/rest/collaborators/collaborators
               https://docs.github.com/en/rest/teams/teams
  API version: X-GitHub-Api-Version 2026-03-10
  Auth:        bearer token. The token may be a personal access token, an org or
               GitHub App installation token, or any RBAC scoped credential. The
               connector does not assume a scope; it maps whatever the credential
               can reach. Needs read access to repository contents and metadata,
               and to members for ACL sync.
  Pagination:  per_page up to 100, follow the Link header rel="next".
  Incremental: list commits with `since`, then read each commit's changed files.
  Rate limit:  5000 requests per hour authenticated; surfaced, not hidden.
  ACL model:   a public repo maps to public. A private repo maps to the
               collaborators (users) and teams (groups) that the credential can
               see, namespaced by the instance. Because access is RBAC controlled,
               the ACL reflects the credential's actual reach, not an assumption.
  Verified on: 2026-06-08.

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
        self.meta = ConnectorMeta(
            name="github",
            auth=AuthScheme.api_token,
            capabilities=Capabilities(
                incremental=True, permission_sync=True, deletions=True, slim_listing=True
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
                ),
                ConfigField(
                    key="repo",
                    label="Repository",
                    kind=FieldKind.STRING,
                    placeholder="Hello-World",
                    help="The repository name, without the owner prefix.",
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
            source = (
                self._all_paths(client, branch)
                if cursor is None
                else self._changed_paths(client, cursor)
            )
            count = 0
            async for path in source:
                if self._path and not path.startswith(self._path):
                    continue
                yield self._ref(path), next_cursor
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

    # --- helpers ---
    def _client(self) -> httpx.AsyncClient:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _API_VERSION,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=30.0)

    def _ref(self, path: str) -> DocRef:
        return DocRef(source_doc_id=path, instance_id=self.instance_id)

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

    async def _changed_paths(self, client: httpx.AsyncClient, since: str) -> AsyncIterator[str]:
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
                    if f.get("status") != "removed" and _is_textual(path) and path not in seen:
                        seen.add(path)
                        yield path
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
        if not repo.get("private", False) or repo.get("visibility") == "public":
            self._acl_cache = AccessControl(public=True, principal_ns=ns)
            return self._acl_cache
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
