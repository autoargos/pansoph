"""Hermetic conformance for the confluence connector, driven by respx mocks.

No live API token: the Confluence Cloud content list and content-with-restrictions
calls are mocked with representative payloads. The permission category asserts the
page read restrictions map to allow_users (accountId) and allow_groups (group name).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_confluence.connector import ConfluenceConnector
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions

_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> ConfluenceConnector:
    return ConfluenceConnector(
        instance_id="conf",
        config={"base_url": "https://acme.atlassian.net", "email": "e@x.com", "token": "x"},
    )


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_confluence_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/wiki/rest/api/content").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "p1"}]})
    )
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["p1"]


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
    respx_mock.get(path="/wiki/rest/api/content/p1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "p1",
                "restrictions": {
                    "read": {
                        "restrictions": {
                            "user": {"results": [{"accountId": "alice"}]},
                            "group": {"results": [{"name": "eng"}]},
                        }
                    }
                },
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
