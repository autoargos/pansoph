"""Hermetic conformance for the Microsoft Teams connector, driven by respx mocks.

No live credentials: every Microsoft Graph call the connector makes is mocked with
a representative payload. The permission category asserts the channel ACL maps to
the channel members (the userId of each conversationMember) the credential can see.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_connector_teams.connector import TeamsConnector

_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> TeamsConnector:
    return TeamsConnector(instance_id="teams", config={"token": "x", "team_id": "T1"})


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_teams_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/v1.0/teams/T1/channels").mock(
        return_value=httpx.Response(200, json={"value": [{"id": "C1"}]})
    )
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["C1"]


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_teams_permissions(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/v1.0/teams/T1/channels/C1/members").mock(
        return_value=httpx.Response(200, json={"value": [{"userId": "alice"}, {"userId": "bob"}]})
    )
    conn = _factory()
    await check_permissions(
        conn,
        source_acl=_FIX / "principals.json",
        principals=_FIX / "principals.json",
        expected=_FIX / "expected.json",
    )
