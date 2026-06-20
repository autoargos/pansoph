"""Hermetic conformance for the slack connector, driven by respx mocks.

No live credentials: every Slack Web API call the connector makes is mocked with a
representative payload. The permission category asserts a private channel ACL maps to
its member user ids, with no groups and no deny (Slack exposes neither per channel).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_connector_slack.connector import SlackConnector

_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> SlackConnector:
    return SlackConnector(
        instance_id="acme",
        config={"token": "xoxb-test", "base_url": "https://slack.com"},
    )


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_slack_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/api/conversations.list").mock(
        return_value=httpx.Response(
            200,
            json={
                "channels": [
                    {"id": "C1", "is_private": False},
                    {"id": "C2", "is_private": True},
                ],
                "response_metadata": {"next_cursor": ""},
            },
        )
    )
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["C1", "C2"]


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_slack_permissions(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/api/conversations.info").mock(
        return_value=httpx.Response(200, json={"channel": {"id": "C2", "is_private": True}})
    )
    respx_mock.get(path="/api/conversations.members").mock(
        return_value=httpx.Response(
            200, json={"members": ["U1", "U2"], "response_metadata": {"next_cursor": ""}}
        )
    )
    conn = _factory()
    await check_permissions(
        conn,
        source_acl=_FIX / "principals.json",
        principals=_FIX / "principals.json",
        expected=_FIX / "expected.json",
    )
