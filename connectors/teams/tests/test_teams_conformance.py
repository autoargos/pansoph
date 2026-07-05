"""Hermetic conformance for the Microsoft Teams connector, driven by respx mocks.

No live credentials: every Microsoft Graph call the connector makes is mocked with
a representative payload. The permission category asserts the channel ACL maps to
the users who can actually see the channel, which depends on the channel's
membershipType: a standard channel inherits the team roster (team members), and a
shared channel exposes direct plus indirect members (allMembers). The plain
channel/members endpoint returns only direct members and would under-share both, so
the connector must not use it for the ACL. These tests pin that behavior.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_connector_teams.connector import TeamsConnector
from pansoph_types import DocRef

_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> TeamsConnector:
    return TeamsConnector(instance_id="teams", config={"token": "x", "team_id": "T1"})


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_teams_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/v1.0/teams/T1/channels").mock(
        return_value=httpx.Response(
            200, json={"value": [{"id": "C1", "membershipType": "standard"}]}
        )
    )
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["C1"]


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_teams_permissions(respx_mock: respx.MockRouter) -> None:
    # C1 is a standard channel: its ACL is the team roster, not the channel's direct
    # members. The channel/members endpoint is deliberately NOT mocked; if the
    # connector called it (the old under-sharing bug) the request would error.
    respx_mock.get(path="/v1.0/teams/T1/channels/C1").mock(
        return_value=httpx.Response(200, json={"id": "C1", "membershipType": "standard"})
    )
    respx_mock.get(path="/v1.0/teams/T1/members").mock(
        return_value=httpx.Response(200, json={"value": [{"userId": "alice"}, {"userId": "bob"}]})
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
async def test_teams_shared_channel_uses_allmembers_and_dedupes(
    respx_mock: respx.MockRouter,
) -> None:
    # A shared channel maps from allMembers (direct plus indirect). allMembers can
    # repeat a userId once per sharing path, so the ACL must deduplicate. The plain
    # members endpoint is not mocked; using it here would under-share (drop dave, the
    # indirect member) and is the bug this test guards against.
    respx_mock.get(path="/v1.0/teams/T1/channels/C2").mock(
        return_value=httpx.Response(200, json={"id": "C2", "membershipType": "shared"})
    )
    respx_mock.get(path="/v1.0/teams/T1/channels/C2/allMembers").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {"userId": "carol"},
                    {"userId": "dave"},
                    {"userId": "carol"},
                    # An anonymous guest member carries no userId and is skipped.
                    {"displayName": "guest"},
                ]
            },
        )
    )
    conn = _factory()
    acl = await conn.list_acls(DocRef(source_doc_id="C2", instance_id="teams"))
    assert acl.allow_users == ["carol", "dave"]
    assert acl.principal_ns == "teams"
    assert acl.allow_groups == []
    assert acl.deny == []
