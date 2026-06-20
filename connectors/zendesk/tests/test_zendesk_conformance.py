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
