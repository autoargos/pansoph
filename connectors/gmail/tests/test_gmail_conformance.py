"""Hermetic conformance for the gmail connector, driven by respx mocks.

No live credentials: the messages list call the connector makes is mocked with a
representative payload. The permission category needs no HTTP, because a Gmail
mailbox is private to its owner and the ACL is fully determined by config, so it
asserts the owner only ACL directly against the fixtures.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_gmail.connector import GmailConnector
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions

_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> GmailConnector:
    return GmailConnector(
        instance_id="alice",
        config={"token": "x", "user_email": "alice@x.com"},
    )


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_gmail_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/gmail/v1/users/me/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "m1", "threadId": "t1"}]})
    )
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["m1"]


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_gmail_permissions(respx_mock: respx.MockRouter) -> None:
    conn = _factory()
    await check_permissions(
        conn,
        source_acl=_FIX / "principals.json",
        principals=_FIX / "principals.json",
        expected=_FIX / "expected.json",
    )
