"""Hermetic conformance for the zendesk connector, driven by respx mocks.

No live credentials: every Zendesk Help Center call the connector makes is mocked
with a representative payload. The suite exercises the full contract via
`assert_conforms`, the deletions contract via reconciliation, the draft-skip policy,
and the user-segment ACL mapping (staff, group, org, or_tags, tags conjunction fail
closed, and added_user_ids), exactly as the source models each one.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_sdk import assert_conforms
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_connector_sdk.verify.categories.sync import check_deletions
from pansoph_connector_sdk.verify.principals import Principal, can_see
from pansoph_connector_zendesk.connector import ZendeskConnector
from pansoph_types import DocRef

_FIX = Path(__file__).parent / "fixtures" / "permissions"
_CONFIG = {
    "subdomain": "acme",
    "email": "e@x.com",
    "token": "t",
    "base_url": "https://acme.zendesk.com",
}


def _factory() -> ZendeskConnector:
    return ZendeskConnector(instance_id="acme", config=_CONFIG)


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_zendesk_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/api/v2/help_center/en-us/articles.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "articles": [{"id": 1, "title": "A", "body": "x", "user_segment_id": None}],
                "meta": {"has_more": False},
                "links": {"next": None},
            },
        )
    )
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["1"]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_zendesk_list_changes_yields_non_empty_cursor(respx_mock: respx.MockRouter) -> None:
    # The resume cursor must be non-empty so the conformance contract holds and the
    # runtime can persist and replay it for deletion reconciliation.
    respx_mock.get(path="/api/v2/help_center/en-us/articles.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "articles": [{"id": 1, "title": "A", "body": "x", "user_segment_id": None}],
                "meta": {"has_more": False},
                "links": {"next": None},
            },
        )
    )
    conn = _factory()
    cursors = [cursor async for _ref, cursor in conn.list_changes(None)]
    assert cursors and all(cursors), "every yielded cursor must be non-empty"


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_zendesk_conforms(respx_mock: respx.MockRouter) -> None:
    # assert_conforms seeds via list_changes, then fetch and list_acls on the first
    # ref. Article 1 is public; the listing has no drafts and closes in one page.
    respx_mock.get(path="/api/v2/help_center/en-us/articles.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "articles": [{"id": 1, "title": "A", "body": "x", "user_segment_id": None}],
                "meta": {"has_more": False},
                "links": {"next": None},
            },
        )
    )
    respx_mock.get(path="/api/v2/help_center/articles/1.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "article": {
                    "id": 1,
                    "title": "A",
                    "body": "x",
                    "user_segment_id": None,
                    "html_url": "h",
                    "draft": False,
                }
            },
        )
    )
    conn = _factory()
    await assert_conforms(conn)


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_zendesk_paginates_all_pages(respx_mock: respx.MockRouter) -> None:
    # Two pages: the first carries has_more true and a links.next on the same path,
    # the second closes it out. side_effect serves them in order.
    respx_mock.get(path="/api/v2/help_center/en-us/articles.json").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "articles": [{"id": 1, "title": "A", "body": "x", "user_segment_id": None}],
                    "meta": {"has_more": True},
                    "links": {
                        "next": "https://acme.zendesk.com/api/v2/help_center/en-us/articles.json?page[after]=N"
                    },
                },
            ),
            httpx.Response(
                200,
                json={
                    "articles": [{"id": 2, "title": "B", "body": "y", "user_segment_id": None}],
                    "meta": {"has_more": False},
                    "links": {"next": None},
                },
            ),
        ]
    )
    conn = _factory()
    ids = [ref.source_doc_id async for ref, _ in conn.list_changes(None)]
    assert ids == ["1", "2"]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_zendesk_skips_draft_articles_in_listing(respx_mock: respx.MockRouter) -> None:
    # A draft (unpublished) article must never be listed for indexing: with Basic auth
    # the agent credential sees drafts, but end users must not retrieve them.
    respx_mock.get(path="/api/v2/help_center/en-us/articles.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "articles": [
                    {"id": 1, "title": "pub", "body": "x", "user_segment_id": None, "draft": False},
                    {"id": 2, "title": "wip", "body": "y", "user_segment_id": None, "draft": True},
                ],
                "meta": {"has_more": False},
                "links": {"next": None},
            },
        )
    )
    conn = _factory()
    ids = [ref.source_doc_id async for ref, _ in conn.list_changes(None)]
    assert ids == ["1"], "draft article 2 must be skipped in the listing"


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_zendesk_draft_fetch_fails_closed(respx_mock: respx.MockRouter) -> None:
    # Defense in depth: if a draft reaches fetch (a race), it must not surface content
    # and its ACL must be fail-closed so no end user can retrieve it.
    respx_mock.get(path="/api/v2/help_center/articles/9.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "article": {
                    "id": 9,
                    "title": "wip",
                    "body": "secret draft",
                    "user_segment_id": None,
                    "html_url": "h",
                    "draft": True,
                }
            },
        )
    )
    conn = _factory()
    raw, acl = await conn.fetch(DocRef(source_doc_id="9", instance_id="acme"))
    assert raw.text is None, "a draft must not surface its body"
    assert acl.public is False
    assert acl.allow_groups == ["unsatisfiable:tags_and"], "draft ACL must fail closed"


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_zendesk_deletions_reconcile(respx_mock: respx.MockRouter) -> None:
    # Run one: articles 1 and 2 are published. Record the resume cursor.
    respx_mock.get(path="/api/v2/help_center/en-us/articles.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "articles": [
                    {"id": 1, "title": "A", "body": "x", "user_segment_id": None, "draft": False},
                    {"id": 2, "title": "B", "body": "y", "user_segment_id": None, "draft": False},
                ],
                "meta": {"has_more": False},
                "links": {"next": None},
            },
        )
    )
    conn = _factory()
    first_cursor: str | None = None
    async for _ref, cursor in conn.list_changes(None):
        first_cursor = cursor
    assert first_cursor

    # Run two: article 2 is gone. Reconciliation must tombstone it and only it.
    respx_mock.reset()
    respx_mock.get(path="/api/v2/help_center/en-us/articles.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "articles": [
                    {"id": 1, "title": "A", "body": "x", "user_segment_id": None, "draft": False},
                ],
                "meta": {"has_more": False},
                "links": {"next": None},
            },
        )
    )
    conn2 = _factory()
    seen: dict[str, bool] = {}
    async for ref, _cursor in conn2.list_changes(first_cursor):
        seen[ref.source_doc_id] = ref.deleted
    assert seen == {"1": False, "2": True}, "article 2 must reconcile as a deletion tombstone"


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_zendesk_check_deletions_contract(respx_mock: respx.MockRouter) -> None:
    # The shared deletions category: listing from a cursor recorded before a removal
    # must surface at least one deleted DocRef. The deleted_state encodes ids {1, 2};
    # the current listing has only {1}, so id 2 reconciles as a tombstone.
    respx_mock.get(path="/api/v2/help_center/en-us/articles.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "articles": [
                    {"id": 1, "title": "A", "body": "x", "user_segment_id": None, "draft": False},
                ],
                "meta": {"has_more": False},
                "links": {"next": None},
            },
        )
    )
    conn = _factory()
    deleted_state = ZendeskConnector._encode_cursor({"1", "2"})
    await check_deletions(conn, deleted_state)


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_zendesk_permissions(respx_mock: respx.MockRouter) -> None:
    # Article 2: staff user_type with a group restriction -> allow_groups
    # ["staff"? no] the group restriction narrows it to group:7 only.
    respx_mock.get(path="/api/v2/help_center/articles/2.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "article": {
                    "id": 2,
                    "title": "B",
                    "body": "y",
                    "user_segment_id": 50,
                    "html_url": "h",
                    "draft": False,
                }
            },
        )
    )
    respx_mock.get(path="/api/v2/help_center/user_segments/50.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "user_segment": {
                    "user_type": "staff",
                    "group_ids": [7],
                    "organization_ids": [],
                }
            },
        )
    )
    # Article 3: signed_in with or_tags plus an explicitly added user.
    respx_mock.get(path="/api/v2/help_center/articles/3.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "article": {
                    "id": 3,
                    "title": "C",
                    "body": "z",
                    "user_segment_id": 60,
                    "html_url": "h",
                    "draft": False,
                }
            },
        )
    )
    respx_mock.get(path="/api/v2/help_center/user_segments/60.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "user_segment": {
                    "user_type": "signed_in_users",
                    "group_ids": [],
                    "organization_ids": [],
                    "or_tags": ["vip"],
                    "added_user_ids": [999],
                }
            },
        )
    )
    # Article 4: signed_in restricted by a tags conjunction -> fail closed, not
    # widened to all signed-in users.
    respx_mock.get(path="/api/v2/help_center/articles/4.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "article": {
                    "id": 4,
                    "title": "D",
                    "body": "w",
                    "user_segment_id": 70,
                    "html_url": "h",
                    "draft": False,
                }
            },
        )
    )
    respx_mock.get(path="/api/v2/help_center/user_segments/70.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "user_segment": {
                    "user_type": "signed_in_users",
                    "group_ids": [],
                    "organization_ids": [],
                    "tags": ["confidential", "legal"],
                }
            },
        )
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
async def test_zendesk_cross_category_and_segment_fails_closed_no_or_leak(
    respx_mock: respx.MockRouter,
) -> None:
    # Regression for the cross-category AND over-share leak. Segment 80 restricts
    # signed_in users by organization_ids=[9] AND or_tags=["vip"]: the Zendesk reach is
    # signed-in users who are in org 9 AND hold the vip tag. The old mapping flattened
    # this into allow_groups=["org:9", "tag:vip"], and the pure-OR resolver then granted
    # ANY vip-tagged user (even in a different org) and ANY org-9 member (even without
    # the vip tag). That is an over-grant / leak. The connector must now fail closed:
    # emit only the unsatisfiable cross-category sentinel, granting neither side.
    respx_mock.get(path="/api/v2/help_center/articles/8.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "article": {
                    "id": 8,
                    "title": "H",
                    "body": "cross-category secret",
                    "user_segment_id": 80,
                    "html_url": "h",
                    "draft": False,
                }
            },
        )
    )
    respx_mock.get(path="/api/v2/help_center/user_segments/80.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "user_segment": {
                    "user_type": "signed_in_users",
                    "group_ids": [],
                    "organization_ids": [9],
                    "or_tags": ["vip"],
                }
            },
        )
    )
    conn = _factory()
    _raw, acl = await conn.fetch(DocRef(source_doc_id="8", instance_id="acme"))

    # The mapping must not leak either narrowing category into the union.
    assert acl.public is False
    assert acl.allow_groups == ["unsatisfiable:cross_category_and"], (
        "a cross-category AND segment must fail closed, not flatten to an OR union"
    )
    assert "org:9" not in acl.allow_groups
    assert "tag:vip" not in acl.allow_groups

    # The exact previously-leaked principal: a vip-tagged user in a DIFFERENT org. Under
    # the old OR mapping this principal was granted; it must now be DENIED.
    leaked_principal = Principal(user_id="vip_other_org", groups=["tag:vip"])
    assert not can_see(acl, leaked_principal), (
        "vip-tagged principal outside org 9 must NOT see a segment requiring org 9 AND vip"
    )


@pytest.mark.conformance
@pytest.mark.permission
async def test_zendesk_staff_plus_tag_segment_fails_closed(respx_mock: respx.MockRouter) -> None:
    # Regression for the residual staff-base leak found in re-verify. A segment
    # {user_type: "staff", or_tags: ["vip"]} means "agents/admins who ALSO hold the vip
    # tag" (user_type is an AND base constraint). The prior fix did not count user_type as
    # a narrowing category, so this had narrowing_count==1 and mapped to allow_groups=
    # ["tag:vip"], DROPPING the staff constraint. But Zendesk tags are shared across staff
    # AND end users, so tag:vip grants a non-staff end user with the vip tag, who cannot
    # read the staff-only article. The fix counts staff as narrowing when paired with a
    # tag/org category, so this fails closed.
    respx_mock.get(path="/api/v2/help_center/articles/9.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "article": {
                    "id": 9,
                    "title": "S",
                    "body": "staff-only vip note",
                    "user_segment_id": 81,
                    "html_url": "h",
                    "draft": False,
                }
            },
        )
    )
    respx_mock.get(path="/api/v2/help_center/user_segments/81.json").mock(
        return_value=httpx.Response(
            200,
            json={"user_segment": {"user_type": "staff", "group_ids": [], "or_tags": ["vip"]}},
        )
    )
    conn = _factory()
    _raw, acl = await conn.fetch(DocRef(source_doc_id="9", instance_id="acme"))

    assert acl.public is False
    assert acl.allow_groups == ["unsatisfiable:cross_category_and"], (
        "staff AND tag is inexpressible in the OR model and must fail closed"
    )
    assert "tag:vip" not in acl.allow_groups, "the staff base constraint must not be dropped"
    # A non-staff end user holding the vip tag: reachable under the old drop-staff mapping,
    # must now be denied.
    end_user = Principal(user_id="enduser_vip", groups=["tag:vip"])
    assert not can_see(acl, end_user), (
        "a non-staff vip-tagged end user must not see a staff segment"
    )
    # The other leaked side: an org-9 member without the vip tag must also be denied.
    org_only_principal = Principal(user_id="org9_no_tag", groups=["org:9"])
    assert not can_see(acl, org_only_principal), (
        "org-9 principal without the vip tag must NOT see a segment requiring org 9 AND vip"
    )


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_zendesk_segment_fetched_once_per_segment(respx_mock: respx.MockRouter) -> None:
    # Two articles share user_segment_id 50. The segment reach is constant for the
    # sync run, so the segment lookup must run once and be reused, not refetched per
    # article. Each article body still fetches per document.
    article_route_3 = respx_mock.get(path="/api/v2/help_center/articles/3.json").mock(
        return_value=httpx.Response(
            200,
            json={"article": {"id": 3, "title": "C", "body": "z", "user_segment_id": 50}},
        )
    )
    article_route_4 = respx_mock.get(path="/api/v2/help_center/articles/4.json").mock(
        return_value=httpx.Response(
            200,
            json={"article": {"id": 4, "title": "D", "body": "w", "user_segment_id": 50}},
        )
    )
    segment_route = respx_mock.get(path="/api/v2/help_center/user_segments/50.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "user_segment": {
                    "user_type": "staff",
                    "group_ids": [7],
                    "organization_ids": [],
                }
            },
        )
    )
    conn = _factory()
    _, acl_3 = await conn.fetch(DocRef(source_doc_id="3", instance_id="acme"))
    _, acl_4 = await conn.fetch(DocRef(source_doc_id="4", instance_id="acme"))
    # A group-restricted staff segment maps to the group ids only, not to all staff.
    assert acl_3.allow_groups == acl_4.allow_groups == ["group:7"]
    assert segment_route.call_count == 1
    assert article_route_3.call_count == 1
    assert article_route_4.call_count == 1
