"""Hermetic conformance for the zendesk connector, driven by respx mocks.

No live credentials: every Zendesk Help Center call the connector makes is mocked
with a representative payload. The permission category asserts a restricted article's
ACL maps to the user segment's reach (a staff user_type plus its group ids), with no
deny entries, exactly as the source models it.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
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
async def test_zendesk_paginates_all_pages(respx_mock: respx.MockRouter) -> None:
    # Two pages: the first carries has_more true and a links.next on the same
    # path, the second closes it out. side_effect serves them in order, and the
    # path= lookup matches both calls since links.next resolves to the same path.
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
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_zendesk_permissions(respx_mock: respx.MockRouter) -> None:
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
    conn = _factory()
    await check_permissions(
        conn,
        source_acl=_FIX / "principals.json",
        principals=_FIX / "principals.json",
        expected=_FIX / "expected.json",
    )


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_zendesk_segment_fetched_once_per_segment(respx_mock: respx.MockRouter) -> None:
    # Two articles share user_segment_id 50. The segment reach is constant for the
    # sync run, so the segment lookup must run once and be reused, not refetched
    # per article. Each article body still fetches per document; only the shared
    # segment call is cached.
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
    # Both articles resolve to the same reach, and the segment call ran exactly once.
    assert acl_3.allow_groups == acl_4.allow_groups == ["staff", "group:7"]
    assert segment_route.call_count == 1
    assert article_route_3.call_count == 1
    assert article_route_4.call_count == 1
