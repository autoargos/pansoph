"""Hermetic conformance for the notion connector, driven by respx mocks.

No live credentials: the Notion REST calls the connector makes are mocked with
representative payloads. The permission category asserts the connector reports the
operator CONFIGURED default ACL, because Notion exposes no per page permission API
(see the connector module header for the loud caveat).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_notion.connector import NotionConnector
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions

_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> NotionConnector:
    return NotionConnector(
        instance_id="acme",
        config={"token": "x", "allow_groups": ["everyone"]},
    )


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_notion_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(path="/v1/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"id": "p1", "object": "page"}],
                "has_more": False,
                "next_cursor": None,
            },
        )
    )
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["p1"]


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_notion_permissions(respx_mock: respx.MockRouter) -> None:
    # No HTTP is needed: list_acls returns the operator configured default ACL.
    conn = _factory()
    await check_permissions(
        conn,
        source_acl=_FIX / "principals.json",
        principals=_FIX / "principals.json",
        expected=_FIX / "expected.json",
    )
