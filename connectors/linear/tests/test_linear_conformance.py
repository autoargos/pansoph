"""Hermetic conformance for the linear connector, driven by respx mocks.

No live credentials: every Linear GraphQL call the connector makes is mocked with a
representative payload. Both list_changes and fetch/list_acls hit the same /graphql
path, so each test mocks the single response its call under test expects.

The ACL split (verified against https://linear.app/docs/private-teams, 2026-07-05) is
pinned by two tests: a PRIVATE team maps to allow_groups=[team_id] (closed to
non-members), and a NON-PRIVATE (workspace) team maps to public=True (visible to every
workspace member, not just the team). The shared assert_conforms harness is exercised
so the contract, incremental, and deletions branches are all driven by the declared
Capabilities, and the non-empty-cursor invariant is enforced.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_linear.connector import LinearConnector
from pansoph_connector_sdk import assert_conforms
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_types import AccessControl

_FIX = Path(__file__).parent / "fixtures" / "permissions"

# A representative issue whose team is PRIVATE. Reused by the permission fixture test
# and the assert_conforms fetch/list_acls branch.
_PRIVATE_ISSUE = {
    "id": "i1",
    "title": "A",
    "description": "d",
    "team": {"id": "TEAM1", "key": "ENG", "private": True},
}


def _factory() -> LinearConnector:
    return LinearConnector(instance_id="acme", config={"token": "k"})


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_linear_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(path="/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": [{"id": "i1", "title": "A", "updatedAt": "2026-07-05T00:00:00Z"}],
                        "pageInfo": {"hasNextPage": False, "endCursor": "E1"},
                    }
                }
            },
        )
    )
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["i1"]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_linear_list_changes_yields_nonempty_cursor(respx_mock: respx.MockRouter) -> None:
    # The updatedAt is the durable per-doc cursor; it must be non-empty so the
    # assert_conforms contract holds and resume is meaningful.
    respx_mock.post(path="/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": [{"id": "i1", "title": "A", "updatedAt": "2026-07-05T00:00:00Z"}],
                        "pageInfo": {"hasNextPage": False, "endCursor": "E1"},
                    }
                }
            },
        )
    )
    conn = _factory()
    pairs = [(ref.source_doc_id, cursor) async for ref, cursor in conn.list_changes(None)]
    assert pairs == [("i1", "2026-07-05T00:00:00Z")]
    for _, cursor in pairs:
        assert isinstance(cursor, str) and cursor


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_linear_paginates_all_pages(respx_mock: respx.MockRouter) -> None:
    page1 = httpx.Response(
        200,
        json={
            "data": {
                "issues": {
                    "nodes": [{"id": "i1", "title": "A", "updatedAt": "2026-07-01T00:00:00Z"}],
                    "pageInfo": {"hasNextPage": True, "endCursor": "E1"},
                }
            }
        },
    )
    page2 = httpx.Response(
        200,
        json={
            "data": {
                "issues": {
                    "nodes": [{"id": "i2", "title": "B", "updatedAt": "2026-07-02T00:00:00Z"}],
                    "pageInfo": {"hasNextPage": False, "endCursor": "E2"},
                }
            }
        },
    )
    respx_mock.post(path="/graphql").mock(side_effect=[page1, page2])
    conn = _factory()
    ids = [ref.source_doc_id async for ref, _ in conn.list_changes(None)]
    assert ids == ["i1", "i2"]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_linear_conforms(respx_mock: respx.MockRouter) -> None:
    # assert_conforms lists once (needs a non-empty cursor) then fetch/list_acls the
    # first ref. One respx route serves both calls; whichever query arrives, the JSON
    # carries both an "issues" page and an "issue", so respx does not need per-call
    # branching. incremental=False and deletions=False mean the harness skips those
    # replay branches, matching the declared Capabilities.
    respx_mock.post(path="/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": [{"id": "i1", "title": "A", "updatedAt": "2026-07-05T00:00:00Z"}],
                        "pageInfo": {"hasNextPage": False, "endCursor": "E1"},
                    },
                    "issue": _PRIVATE_ISSUE,
                }
            },
        )
    )
    conn = _factory()
    await assert_conforms(conn)


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_linear_permissions_private_team(respx_mock: respx.MockRouter) -> None:
    # Private team: only members see the issue, so allow_groups=[team_id].
    respx_mock.post(path="/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"issue": _PRIVATE_ISSUE}})
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
async def test_linear_public_team_is_workspace_visible(respx_mock: respx.MockRouter) -> None:
    # Non-private team: visible to ALL workspace members, so public=True, NOT
    # allow_groups=[team_id]. This is the finding the audit flagged: mapping a public
    # team to a group would over-deny workspace members who are not on the team.
    public_issue = {
        "id": "i2",
        "title": "B",
        "description": "d",
        "team": {"id": "TEAM2", "key": "OPS", "private": False},
    }
    respx_mock.post(path="/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"issue": public_issue}})
    )
    conn = _factory()
    acl = await conn.list_acls(conn._ref("i2"))
    assert acl == AccessControl(public=True, principal_ns="acme")
    # A workspace member who is on no matching group still sees a public-team issue.
    assert acl.public is True and acl.allow_groups == []
