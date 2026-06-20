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
