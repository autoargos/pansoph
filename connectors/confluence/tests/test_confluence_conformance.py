"""Hermetic conformance for the confluence connector, driven by respx mocks.

No live API token: the Confluence Cloud content search (v1 CQL), the v2 page reads, the
byOperation read-restriction endpoint, the v2 trashed-page list, and the v2
space-permissions calls are mocked with representative payloads.
The suite pins:
  - assert_conforms end to end (a real non-empty lastmodified cursor, no leak on
    replay, discovery, fetch, list_acls, resource discovery),
  - the ACL cascade: a restricted ancestor is NOT widened to the space audience,
  - the mandatory space-View intersection: a restriction principal that lacks space
    View is DROPPED (a restricted page never over-shares to a broad group inside a
    narrow space), and an absent restriction expansion fails closed,
  - deletions: a trashed page surfaces as a tombstone,
  - the space-wide fallback: real space View principals (accountId, groupId, role),
  - the direct page restriction: groups keyed by the stable groupId.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_confluence.connector import ConfluenceConnector
from pansoph_connector_sdk import assert_conforms
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_connector_sdk.verify.categories.sync import check_deletions
from pansoph_types import DocRef

_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> ConfluenceConnector:
    return ConfluenceConnector(
        instance_id="conf",
        config={"base_url": "https://acme.atlassian.net", "email": "e@x.com", "token": "x"},
    )


def _space_scoped_factory() -> ConfluenceConnector:
    return ConfluenceConnector(
        instance_id="conf",
        config={
            "base_url": "https://acme.atlassian.net",
            "email": "e@x.com",
            "token": "x",
            "space_key": "ENG",
        },
    )


def _one_page_search(respx_mock: respx.MockRouter, when: str = "2026-07-05T12:34:56.000Z") -> None:
    respx_mock.get(path="/wiki/rest/api/content/search").mock(
        return_value=httpx.Response(
            200,
            json={"results": [{"id": "p1", "version": {"when": when}}]},
        )
    )


def _mock_v2_page(
    respx_mock: respx.MockRouter,
    pid: str,
    *,
    space_id: str = "10",
    parent_id: str | None = None,
    title: str = "Page",
    body: str = "hello",
) -> None:
    """Mock GET /wiki/api/v2/pages/{pid}, the v2 content read the connector now uses.

    Carries spaceId and parentId (v2 exposes only the immediate parent, so the ACL
    cascade walks the parentId chain), plus a storage body and a _links.webui for the
    document URL.
    """
    respx_mock.get(path=f"/wiki/api/v2/pages/{pid}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": pid,
                "status": "current",
                "title": title,
                "spaceId": space_id,
                "parentId": parent_id,
                "body": {"storage": {"representation": "storage", "value": body}},
                "_links": {"base": "https://acme.atlassian.net/wiki", "webui": f"/pages/{pid}"},
            },
        )
    )


def _mock_restriction(
    respx_mock: respx.MockRouter,
    pid: str,
    *,
    users: tuple[str, ...] = (),
    groups: tuple[str, ...] = (),
) -> None:
    """Mock GET /wiki/rest/api/content/{pid}/restriction/byOperation/read.

    This is the one v1 content endpoint that accepts a granular token; a 200 with empty
    result sets means the content is unrestricted. Groups carry a stable groupId.
    """
    respx_mock.get(path=f"/wiki/rest/api/content/{pid}/restriction/byOperation/read").mock(
        return_value=httpx.Response(
            200,
            json={
                "operation": "read",
                "restrictions": {
                    "user": {"results": [{"accountId": u} for u in users]},
                    "group": {"results": [{"id": g, "name": g} for g in groups]},
                },
            },
        )
    )


def _mock_restriction_error(respx_mock: respx.MockRouter, pid: str, status: int = 403) -> None:
    """Mock the restriction read failing, so the ACL builder must fail closed."""
    respx_mock.get(path=f"/wiki/rest/api/content/{pid}/restriction/byOperation/read").mock(
        return_value=httpx.Response(status, json={"code": status, "message": "no"})
    )


def _no_trash(respx_mock: respx.MockRouter) -> None:
    # The non-space factory lists trashed pages via v2 /pages?status=trashed (no per-space
    # enumeration). Discovery still lists spaces, so both v2 endpoints answer here.
    respx_mock.get(path="/wiki/api/v2/spaces").mock(
        return_value=httpx.Response(
            200, json={"results": [{"id": "10", "key": "ENG", "name": "Engineering"}]}
        )
    )
    respx_mock.get(path="/wiki/api/v2/pages").mock(
        return_value=httpx.Response(200, json={"results": []})
    )


def _space_view(
    respx_mock: respx.MockRouter,
    space_id: str = "10",
    users: tuple[str, ...] = ("alice",),
    groups: tuple[str, ...] = ("eng-gid",),
) -> None:
    """Mock the space View audience (GET /wiki/api/v2/spaces/{id}/permissions).

    The space View audience is a mandatory intersection layer for restricted pages,
    so every restricted-page ACL test must answer this endpoint.
    """
    read_op = {"key": "read", "targetType": "space"}
    results = [{"principal": {"type": "user", "id": u}, "operation": read_op} for u in users] + [
        {"principal": {"type": "group", "id": g}, "operation": read_op} for g in groups
    ]
    respx_mock.get(path=f"/wiki/api/v2/spaces/{space_id}/permissions").mock(
        return_value=httpx.Response(200, json={"results": results})
    )


def _restricted_page(respx_mock: respx.MockRouter) -> None:
    """p1 has a direct read restriction (a user and a group keyed by groupId)."""
    _mock_v2_page(respx_mock, "p1", title="Page one")
    _mock_restriction(respx_mock, "p1", users=("alice",), groups=("eng-gid",))


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_confluence_assert_conforms(respx_mock: respx.MockRouter) -> None:
    # One live page with a real version.when (the cursor), a direct restriction
    # intersected with the space View audience (which grants both restriction
    # principals, so the page ACL is non-empty), and no trash. Replaying the last
    # cursor must leak nothing: the same-minute page is filtered by its precise
    # timestamp.
    _one_page_search(respx_mock)
    _no_trash(respx_mock)
    _restricted_page(respx_mock)
    _space_view(respx_mock)
    conn = _factory()
    await assert_conforms(conn)


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_confluence_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    _one_page_search(respx_mock)
    _no_trash(respx_mock)
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["p1"]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_confluence_incremental_cursor_does_not_leak_on_replay(
    respx_mock: respx.MockRouter,
) -> None:
    when = "2026-07-05T12:34:56.000Z"
    _one_page_search(respx_mock, when=when)
    _no_trash(respx_mock)
    conn = _factory()

    first = [(ref.source_doc_id, cursor) async for ref, cursor in conn.list_changes(None)]
    assert first == [("p1", when)]

    # Replaying the exact cursor: the coarse minute floor still returns p1, but the
    # precise version.when filter drops it, so nothing is re-emitted.
    replay = [ref.source_doc_id async for ref, _ in conn.list_changes(when)]
    assert replay == []


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_confluence_deletions_surface_trashed_as_tombstone(
    respx_mock: respx.MockRouter,
) -> None:
    # No upserts; one trashed page in the configured space. The space-scoped factory
    # resolves its space key to a numeric id, then lists v2 trashed pages for that space.
    respx_mock.get(path="/wiki/rest/api/content/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx_mock.get(path="/wiki/api/v2/spaces").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "10", "key": "ENG"}]})
    )
    respx_mock.get(path="/wiki/api/v2/pages").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "gone"}]})
    )
    conn = _space_scoped_factory()

    seen = [
        (ref.source_doc_id, ref.deleted, cursor) async for ref, cursor in conn.list_changes(None)
    ]
    assert seen == [("gone", True, "1970-01-01T00:00:00.000Z")]

    # The shared deletions category must also pass.
    await check_deletions(conn, deleted_state=None)


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_confluence_inherited_restriction_is_not_widened(
    respx_mock: respx.MockRouter,
) -> None:
    """A child with no direct restriction but a restricted ancestor must NOT become
    the space-wide audience. This is the reported over-broad-grant bug."""
    # child -> root -> (top). The ancestor walk reads each v2 page for its parentId, and
    # each content's read restriction from the byOperation endpoint.
    _mock_v2_page(respx_mock, "child", parent_id="root")
    _mock_restriction(respx_mock, "child")  # child itself is unrestricted
    _mock_v2_page(respx_mock, "root", parent_id=None)
    _mock_restriction(respx_mock, "root", groups=("leadership-gid",))
    # Leadership holds space View, so the mandatory space-View intersection preserves
    # the inherited restriction rather than widening it to the space audience.
    _space_view(respx_mock, groups=("leadership-gid",), users=())
    conn = _factory()
    acl = await conn.list_acls(DocRef(source_doc_id="child", instance_id="conf"))

    # The inherited ancestor restriction is the effective audience, not space-wide.
    assert acl.allow_groups == ["leadership-gid"]
    assert acl.allow_users == []
    assert all(not g.startswith("space") for g in acl.allow_groups)
    assert acl.public is False


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_confluence_restriction_group_without_space_view_is_not_leaked(
    respx_mock: respx.MockRouter,
) -> None:
    """Regression, over-share leak (p5 verdict): a page restricted to a broad group
    inside a narrowly-viewable space must NOT grant that whole group.

    Scenario from the verdict: space ENG is viewable only by group "engineering"; a
    page in ENG is restricted to group "all-employees". Confluence effective viewers
    are the intersection (engineering members), so a non-engineering employee who is
    in all-employees but lacks space View cannot see the page. The old code returned
    allow_groups=["all-employees"], leaking the page to the whole company. The fix
    intersects the restriction audience with the space View audience, so the broad
    group is dropped. The leaked principal is now DENIED (the page under-shares, which
    is the safe side). If this test ever fails, the over-share leak has returned.
    """
    _mock_v2_page(respx_mock, "broad", parent_id=None)
    _mock_restriction(respx_mock, "broad", groups=("all-employees",))
    # The space is viewable only by "engineering", which is NOT the restriction group.
    _space_view(respx_mock, groups=("engineering",), users=())
    conn = _factory()
    acl = await conn.list_acls(DocRef(source_doc_id="broad", instance_id="conf"))

    # The previously-leaked broad group is dropped: it holds no space View.
    assert "all-employees" not in acl.allow_groups
    assert acl.allow_groups == []
    assert acl.allow_users == []
    assert acl.public is False


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_confluence_unreadable_restriction_fails_closed(
    respx_mock: respx.MockRouter,
) -> None:
    """A page whose read restriction cannot be read (a non-2xx from the restriction
    endpoint) must fail closed, not fall back to the space-wide audience.

    A 200 with empty result sets means unrestricted; a non-2xx means the audience is
    unconfirmable, so the connector emits the grants-no-one ACL. The space View endpoint
    is deliberately NOT mocked: the own-restriction read fails first, so the builder must
    never reach it. If it did, the unmocked call would surface the regression.
    """
    _mock_v2_page(respx_mock, "noscope", parent_id=None)
    _mock_restriction_error(respx_mock, "noscope", status=403)
    conn = _factory()
    acl = await conn.list_acls(DocRef(source_doc_id="noscope", instance_id="conf"))

    assert acl.allow_users == []
    assert acl.allow_groups == []
    assert acl.public is False


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_confluence_unrestricted_page_maps_real_space_view_audience(
    respx_mock: respx.MockRouter,
) -> None:
    """An unrestricted page (and unrestricted ancestors) falls back to the real
    space View principals: users by accountId, groups by groupId, roles namespaced."""
    _mock_v2_page(respx_mock, "open", parent_id=None)
    _mock_restriction(respx_mock, "open")  # unrestricted: empty result sets
    respx_mock.get(path="/wiki/api/v2/spaces/10/permissions").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "principal": {"type": "user", "id": "carol"},
                        "operation": {"key": "read", "targetType": "space"},
                    },
                    {
                        "principal": {"type": "group", "id": "all-eng"},
                        "operation": {"key": "read", "targetType": "space"},
                    },
                    {
                        "principal": {"type": "role", "id": "role-7"},
                        "operation": {"key": "read", "targetType": "space"},
                    },
                    # A non-view operation must be ignored, never widening the grant.
                    {
                        "principal": {"type": "group", "id": "admins"},
                        "operation": {"key": "administer", "targetType": "space"},
                    },
                ]
            },
        )
    )
    conn = _factory()
    acl = await conn.list_acls(DocRef(source_doc_id="open", instance_id="conf"))
    assert acl.allow_users == ["carol"]
    assert acl.allow_groups == ["all-eng", "space-role:role-7"]
    assert "admins" not in acl.allow_groups


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_confluence_list_resources_parses_and_paginates(
    respx_mock: respx.MockRouter,
) -> None:
    # First page: two spaces plus a _links.next relative URL carrying the opaque
    # cursor. Second page (fetched with that cursor): one space, no _links.next,
    # so the listing is exhausted and next_cursor is None.
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") == "OPAQUE2":
            return httpx.Response(
                200,
                json={
                    "results": [{"id": "300", "key": "OPS", "name": "Operations"}],
                    "_links": {"base": "https://acme.atlassian.net/wiki"},
                },
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {"id": "100", "key": "ENG", "name": "Engineering"},
                    {"id": "200", "key": "MKT", "name": "Marketing"},
                ],
                "_links": {"next": "/wiki/api/v2/spaces?limit=100&cursor=OPAQUE2"},
            },
        )

    respx_mock.get(path="/wiki/api/v2/spaces").mock(side_effect=_handler)
    conn = _factory()

    page1 = await conn.list_resources()
    assert [r.id for r in page1.items] == ["ENG", "MKT"]
    assert [r.label for r in page1.items] == ["Engineering", "Marketing"]
    assert {r.kind for r in page1.items} == {"space"}
    assert all(r.parent_id is None and r.selectable for r in page1.items)
    assert page1.items[0].extra == {"space_id": "100"}
    assert page1.next_cursor == "OPAQUE2"

    page2 = await conn.list_resources(cursor=page1.next_cursor)
    assert [r.id for r in page2.items] == ["OPS"]
    assert page2.next_cursor is None

    # An unknown kind or a parent_id has no children in this flat source.
    empty = await conn.list_resources(kind="folder")
    assert empty.items == [] and empty.next_cursor is None


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_confluence_permissions(respx_mock: respx.MockRouter) -> None:
    # p1 is restricted to alice + eng-gid, and the space View audience grants both,
    # so the intersection preserves exactly {alice, eng-gid} (the expected fixture).
    _restricted_page(respx_mock)
    _space_view(respx_mock)
    conn = _factory()
    await check_permissions(
        conn,
        source_acl=_FIX / "principals.json",
        principals=_FIX / "principals.json",
        expected=_FIX / "expected.json",
    )


@respx.mock(assert_all_called=False)
async def test_oauth_resolves_cloud_id_and_addresses_the_gateway(
    respx_mock: respx.MockRouter,
) -> None:
    # An OAuth instance seals only a token and no base_url. Before its first API call the
    # connector resolves the cloud id from the token (accessible-resources) and addresses
    # the Confluence API through the api.atlassian.com gateway, so discovery no longer
    # fails with a "missing protocol" relative URL.
    resources = respx_mock.get("https://api.atlassian.com/oauth/token/accessible-resources").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": "cid-123", "url": "https://acme.atlassian.net", "name": "acme"}],
        )
    )
    spaces = respx_mock.get(
        "https://api.atlassian.com/ex/confluence/cid-123/wiki/api/v2/spaces"
    ).mock(
        return_value=httpx.Response(
            200, json={"results": [{"id": "10", "key": "ENG", "name": "Engineering"}]}
        )
    )
    conn = ConfluenceConnector(instance_id="conf", config={"oauth_token": "at-xyz"})
    page = await conn.list_resources()
    assert resources.called, "the token's accessible sites must be resolved"
    assert spaces.called, "the API must be addressed through the cloud-id gateway"
    assert [r.id for r in page.items] == ["ENG"]
    assert conn._base == "https://api.atlassian.com/ex/confluence/cid-123"
    # The human-openable link points at the site the user can actually open.
    assert conn._site_web_url == "https://acme.atlassian.net"


@respx.mock(assert_all_called=False)
async def test_oauth_picks_the_configured_site_from_a_multi_site_grant(
    respx_mock: respx.MockRouter,
) -> None:
    # A grant that reaches several sites is disambiguated by the cloud_id config field,
    # so the choice is deterministic rather than dependent on the response order.
    respx_mock.get("https://api.atlassian.com/oauth/token/accessible-resources").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "cid-a", "url": "https://a.atlassian.net"},
                {"id": "cid-b", "url": "https://b.atlassian.net"},
            ],
        )
    )
    respx_mock.get("https://api.atlassian.com/ex/confluence/cid-b/wiki/api/v2/spaces").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    conn = ConfluenceConnector(
        instance_id="conf", config={"oauth_token": "at", "cloud_id": "cid-b"}
    )
    await conn.list_resources()
    assert conn._base == "https://api.atlassian.com/ex/confluence/cid-b"


def test_validate_accepts_oauth_without_base_url() -> None:
    # An OAuth Cloud instance resolves its base at run time, so validate must not demand a
    # base_url for it; a basic-auth instance still must supply one.
    conn = ConfluenceConnector(instance_id="conf", config={})
    assert conn.validate({"oauth_token": "at"}).ok
    assert not conn.validate({}).ok
    assert not conn.validate({"email": "e@x.com", "token": "x"}).ok
    assert conn.validate({"base_url": "https://x", "email": "e@x.com", "token": "x"}).ok
