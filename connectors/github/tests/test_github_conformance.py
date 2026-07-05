"""Hermetic conformance for the github connector, driven by respx mocks.

No live credentials: every GitHub REST call the connector makes is mocked with a
representative payload. Coverage:

- the full shared assert_conforms suite (contract, incremental replay, ACL type),
- check_incremental and check_deletions from the sync category, so the deletions
  capability the connector declares is actually exercised at the gate,
- the ACL mapping for a public, a private, and an internal (GitHub Enterprise) repo,
  since an internal repo is readable by every enterprise member and must NOT map to
  its collaborators alone nor to public.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_github.connector import GithubConnector
from pansoph_connector_sdk.conformance import assert_conforms
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_connector_sdk.verify.categories.sync import check_deletions, check_incremental

_REPO = "/repos/octocat/Hello-World"
_FIX = Path(__file__).parent / "fixtures" / "permissions"

# A cursor recorded just before the source removed a file. Any ISO 8601 timestamp
# works as a `since` value; the mocks below drive the response, not the value.
_DELETED_STATE = "2026-06-01T00:00:00Z"
_LATEST_COMMIT = "2026-07-05T00:00:00Z"


def _factory() -> GithubConnector:
    return GithubConnector(
        instance_id="octocat",
        config={"owner": "octocat", "repo": "Hello-World", "branch": "main", "token": "x"},
    )


def _mock_full_pass(respx_mock: respx.MockRouter) -> None:
    # The full pass (cursor None) reads the repo, the latest commit date (next cursor),
    # and the recursive tree, then fetches file contents on demand.
    respx_mock.get(path=_REPO).mock(
        return_value=httpx.Response(
            200, json={"default_branch": "main", "private": False, "visibility": "public"}
        )
    )
    respx_mock.get(path=f"{_REPO}/commits", params={"per_page": "1"}).mock(
        return_value=httpx.Response(200, json=[{"commit": {"committer": {"date": _LATEST_COMMIT}}}])
    )
    respx_mock.get(path=f"{_REPO}/git/trees/main").mock(
        return_value=httpx.Response(200, json={"tree": [{"type": "blob", "path": "README.md"}]})
    )
    respx_mock.get(url__regex=rf"{_REPO}/contents/.*").mock(
        return_value=httpx.Response(200, json={"content": "", "encoding": "base64"})
    )


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_github_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    _mock_full_pass(respx_mock)
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["README.md"]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_github_assert_conforms(respx_mock: respx.MockRouter) -> None:
    # The full shared suite: contract, fetch/ACL types, list_acls (permission_sync),
    # and the incremental replay invariant. The incremental replay lists from the last
    # cursor; the commits-since mock returns no commits, so nothing already-seen leaks.
    _mock_full_pass(respx_mock)
    respx_mock.get(path=f"{_REPO}/commits", params__contains={"since": _LATEST_COMMIT}).mock(
        return_value=httpx.Response(200, json=[])
    )
    # assert_conforms exercises resource_discovery too, which lists the credential's
    # orgs; the synthetic user owner is resolved from /user.
    respx_mock.get(path="/user").mock(return_value=httpx.Response(200, json={"login": "octocat"}))
    respx_mock.get(path="/user/orgs").mock(return_value=httpx.Response(200, json=[]))
    conn = _factory()
    await assert_conforms(conn)


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_github_incremental_replay_is_clean(respx_mock: respx.MockRouter) -> None:
    # From the last cursor, no already-synced (non-deleted) doc may reappear. The
    # commits-since window is empty, so the replay yields nothing.
    _mock_full_pass(respx_mock)
    respx_mock.get(path=f"{_REPO}/commits", params__contains={"since": _LATEST_COMMIT}).mock(
        return_value=httpx.Response(200, json=[])
    )
    conn = _factory()
    await check_incremental(conn, abnormal_state=None)


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_github_deletions_emit_tombstone(respx_mock: respx.MockRouter) -> None:
    # The deletions gate: listing from a cursor that spans a removal must surface a
    # deleted DocRef for the removed file. The commit detail reports files[].status
    # "removed" for docs.md, which the connector turns into deleted=True.
    respx_mock.get(path=_REPO).mock(
        return_value=httpx.Response(
            200, json={"default_branch": "main", "private": False, "visibility": "public"}
        )
    )
    # list_changes resolves the next cursor from the latest commit before walking the
    # since-window, so both /commits shapes must be mocked.
    respx_mock.get(path=f"{_REPO}/commits", params={"per_page": "1"}).mock(
        return_value=httpx.Response(200, json=[{"commit": {"committer": {"date": _LATEST_COMMIT}}}])
    )
    respx_mock.get(path=f"{_REPO}/commits", params__contains={"since": _DELETED_STATE}).mock(
        return_value=httpx.Response(200, json=[{"sha": "deadbeef"}])
    )
    respx_mock.get(path=f"{_REPO}/commits/deadbeef").mock(
        return_value=httpx.Response(
            200,
            json={
                "files": [
                    {"filename": "docs.md", "status": "removed"},
                    {"filename": "guide.md", "status": "modified"},
                ]
            },
        )
    )
    conn = _factory()

    refs = [ref async for ref, _ in conn.list_changes(_DELETED_STATE)]
    by_id = {r.source_doc_id: r for r in refs}
    assert by_id["docs.md"].deleted is True, "a removed file must be a deleted tombstone"
    assert by_id["guide.md"].deleted is False, "a modified file must be a live upsert"

    # And the shared deletions category passes now that a tombstone is emitted.
    await check_deletions(conn, _DELETED_STATE)


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_github_acl_fetched_once_across_files(respx_mock: respx.MockRouter) -> None:
    # Regression: the ACL is repo level, so fetching it (and the repo metadata) per file
    # doubled API calls and exhausted the unauthenticated rate limit mid-sync. One repo
    # call must serve many fetches.
    repo = respx_mock.get(path=_REPO).mock(
        return_value=httpx.Response(
            200, json={"default_branch": "main", "private": False, "visibility": "public"}
        )
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
        return_value=httpx.Response(
            200, json={"default_branch": "main", "private": True, "visibility": "private"}
        )
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
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_github_acl_internal_repo_maps_to_enterprise_group(
    respx_mock: respx.MockRouter,
) -> None:
    # An internal repo (GitHub Enterprise) carries private=true and visibility="internal"
    # and is readable by every enterprise member. It must map to the synthetic
    # enterprise-members group, NOT to public and NOT to only its collaborators (which
    # would over-deny entitled enterprise members). The collaborators/teams endpoints
    # must not even be consulted for an internal repo.
    respx_mock.get(path=_REPO).mock(
        return_value=httpx.Response(
            200, json={"default_branch": "main", "private": True, "visibility": "internal"}
        )
    )
    collab = respx_mock.get(path=f"{_REPO}/collaborators").mock(
        return_value=httpx.Response(200, json=[{"login": "octocat"}])
    )
    teams = respx_mock.get(path=f"{_REPO}/teams").mock(
        return_value=httpx.Response(200, json=[{"slug": "core-team"}])
    )
    conn = _factory()
    acl = await conn.list_acls(conn._ref("README.md"))

    assert acl.public is False, "an internal repo is not public"
    assert acl.allow_users == [], "an internal repo grants an audience, not named users"
    assert acl.allow_groups == ["enterprise:octocat:members"], (
        "an internal repo maps to the enterprise-members group"
    )
    assert acl.principal_ns == "octocat"
    assert collab.call_count == 0 and teams.call_count == 0, (
        "internal-repo ACL must not fall through to the private collaborators/teams path"
    )


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_github_trimmed_body_does_not_leak_as_public(respx_mock: respx.MockRouter) -> None:
    # Regression (fail-closed public inference): base_url is configurable for GitHub
    # Enterprise or a self-managed gateway, so an untrusted intermediary could return a
    # trimmed GET /repos body that OMITS both `visibility` and `private` on the
    # ACL-decision path. The old code derived public from `not repo.get("private",
    # False)`, so a MISSING `private` field became public=True and published a private
    # repo to the tenant-everyone group, an over-share. The fix requires an explicit
    # visibility == "public"; a trimmed/ambiguous body must fall to the narrower
    # collaborators path and must NEVER be public. Named so the leak cannot silently
    # return.
    respx_mock.get(path=_REPO).mock(
        # No `visibility`, no `private`: the exact trimmed body from the verdict.
        return_value=httpx.Response(200, json={"default_branch": "main"})
    )
    only_collab = respx_mock.get(path=f"{_REPO}/collaborators").mock(
        return_value=httpx.Response(200, json=[{"login": "octocat"}])
    )
    respx_mock.get(path=f"{_REPO}/teams").mock(
        return_value=httpx.Response(200, json=[{"slug": "core-team"}])
    )
    conn = _factory()
    acl = await conn.list_acls(conn._ref("README.md"))

    assert acl.public is False, (
        "a trimmed/ambiguous repo body must NOT be inferred public (over-share leak)"
    )
    # It fails closed to the narrower collaborators audience, under-sharing safely.
    assert acl.allow_users == ["octocat"]
    assert acl.allow_groups == ["core-team"]
    assert acl.principal_ns == "octocat"
    assert only_collab.call_count == 1, "ambiguous body must resolve via the private path"


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
