"""Hermetic conformance for the github connector, driven by respx mocks.

No live credentials: every GitHub REST call the connector makes is mocked with a
representative payload. The permission category asserts the private-repo ACL maps to
the collaborators (users) and teams (groups) the credential can see.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_github.connector import GithubConnector
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions

_REPO = "/repos/octocat/Hello-World"
_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> GithubConnector:
    return GithubConnector(
        instance_id="octocat",
        config={"owner": "octocat", "repo": "Hello-World", "branch": "main", "token": "x"},
    )


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_github_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path=f"{_REPO}/commits").mock(
        return_value=httpx.Response(
            200, json=[{"commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}]
        )
    )
    respx_mock.get(path=f"{_REPO}/git/trees/main").mock(
        return_value=httpx.Response(200, json={"tree": [{"type": "blob", "path": "README.md"}]})
    )
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["README.md"]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_github_acl_fetched_once_across_files(respx_mock: respx.MockRouter) -> None:
    # Regression: the ACL is repo level, so fetching it (and the repo metadata) per file
    # doubled API calls and exhausted the unauthenticated rate limit mid-sync. One repo
    # call must serve many fetches.
    repo = respx_mock.get(path=_REPO).mock(
        return_value=httpx.Response(200, json={"default_branch": "main", "private": False})
    )
    respx_mock.get(url__regex=rf"{_REPO}/contents/.*").mock(
        return_value=httpx.Response(200, json={"content": "", "encoding": "base64"})
    )
    conn = _factory()
    for path in ("a.py", "b.py", "c.py"):
        _, acl = await conn.fetch(conn._ref(path))
        assert acl.public is True
    assert repo.call_count == 1


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_github_permissions_private_repo(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path=_REPO).mock(
        return_value=httpx.Response(200, json={"default_branch": "main", "private": True})
    )
    respx_mock.get(path=f"{_REPO}/collaborators").mock(
        return_value=httpx.Response(200, json=[{"login": "octocat"}])
    )
    respx_mock.get(path=f"{_REPO}/teams").mock(
        return_value=httpx.Response(200, json=[{"slug": "core-team"}])
    )
    conn = _factory()
    await check_permissions(
        conn,
        source_acl=_FIX / "principals.json",
        principals=_FIX / "principals.json",
        expected=_FIX / "expected.json",
    )


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_github_list_resources_orgs_then_repos(respx_mock: respx.MockRouter) -> None:
    # Top level: the authenticated user (synthetic owner) plus the orgs the credential
    # belongs to. Then drilling an org lists its repos, each a selectable owner/repo id.
    respx_mock.get(path="/user").mock(return_value=httpx.Response(200, json={"login": "octocat"}))
    respx_mock.get(path="/user/orgs").mock(
        return_value=httpx.Response(200, json=[{"login": "acme"}, {"login": "globex"}])
    )
    respx_mock.get(path="/orgs/acme/repos").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"name": "widget", "full_name": "acme/widget", "private": True},
                {"name": "public-site", "full_name": "acme/public-site", "private": False},
            ],
        )
    )
    conn = _factory()

    top = await conn.list_resources()
    # The synthetic user owner is first, then the two orgs. All are org-kind owners.
    assert [r.id for r in top.items] == ["octocat", "acme", "globex"]
    assert all(r.kind == "org" and r.parent_id is None for r in top.items)
    assert top.items[0].extra == {"scope": "user"}
    assert top.next_cursor is None

    repos = await conn.list_resources(kind="repo", parent_id="acme")
    assert [r.id for r in repos.items] == ["acme/widget", "acme/public-site"]
    assert [r.label for r in repos.items] == ["widget", "public-site"]
    assert all(r.kind == "repo" and r.parent_id == "acme" for r in repos.items)
    assert repos.items[0].extra == {"private": "true"}
    assert repos.items[1].extra == {"private": "false"}


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_github_list_resources_paginates_by_link_header(respx_mock: respx.MockRouter) -> None:
    # The opaque cursor is GitHub's own page number, parsed out of the Link header
    # rel="next". A first page returns that cursor; the last page returns None.
    respx_mock.get(path="/user").mock(return_value=httpx.Response(200, json={"login": "octocat"}))
    first = httpx.Response(
        200,
        json=[{"name": "r1", "full_name": "acme/r1", "private": False}],
        headers={
            "Link": '<https://api.github.com/orgs/acme/repos?page=2>; rel="next", '
            '<https://api.github.com/orgs/acme/repos?page=2>; rel="last"'
        },
    )
    second = httpx.Response(200, json=[{"name": "r2", "full_name": "acme/r2", "private": False}])
    route = respx_mock.get(path="/orgs/acme/repos").mock(side_effect=[first, second])

    conn = _factory()
    page1 = await conn.list_resources(kind="repo", parent_id="acme")
    assert [r.id for r in page1.items] == ["acme/r1"]
    assert page1.next_cursor == "2"

    page2 = await conn.list_resources(kind="repo", parent_id="acme", cursor=page1.next_cursor)
    assert [r.id for r in page2.items] == ["acme/r2"]
    assert page2.next_cursor is None
    # The second call carried the cursor through as the page query param.
    assert route.calls[1].request.url.params.get("page") == "2"
