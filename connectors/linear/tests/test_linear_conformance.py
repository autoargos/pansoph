"""Hermetic conformance for the linear connector, driven by respx mocks.

No live credentials: every Linear GraphQL call the connector makes is mocked with a
representative payload. Both list_changes and fetch/list_acls hit the same /graphql
path, so each test mocks the single response its call under test expects.

The ACL split (verified against https://linear.app/docs/private-teams and
https://linear.app/docs/members-roles plus the guest-accounts changelog 2022-07-14, on
2026-07-05) is pinned by two tests: a PRIVATE team maps to allow_groups=[team_id]
(closed to non-members), and an OPEN (non-private) team maps to the reserved
full-members group, NOT public=True, because Linear guests are workspace principals who
cannot see open teams and public=True would leak to them. A dedicated regression test
(test_regress_2026_07_05_open_team_denies_guest) pins that non-leak: a guest principal
is DENIED an open-team issue. The shared assert_conforms harness is exercised so the
contract, incremental, and deletions branches are all driven by the declared
Capabilities, and the non-empty-cursor invariant is enforced.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_linear.connector import _WORKSPACE_MEMBERS_GROUP, LinearConnector
from pansoph_connector_sdk import assert_conforms
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_connector_sdk.verify.principals import Principal, can_see
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
async def test_linear_open_team_maps_to_members_group_not_public(
    respx_mock: respx.MockRouter,
) -> None:
    # Open (non-private) team: visible to FULL workspace members but NOT guests, so it
    # maps to the reserved full-members group, NEVER public=True. public=True would
    # short-circuit can_see to True for every principal in the namespace, leaking the
    # issue to guest principals who cannot see open teams in Linear (CLAUDE.md rule 1:
    # over-sharing is a security bug, so fail closed to the narrower audience).
    open_issue = {
        "id": "i2",
        "title": "B",
        "description": "d",
        "team": {"id": "TEAM2", "key": "OPS", "private": False},
    }
    respx_mock.post(path="/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"issue": open_issue}})
    )
    conn = _factory()
    acl = await conn.list_acls(conn._ref("i2"))
    assert acl == AccessControl(allow_groups=[_WORKSPACE_MEMBERS_GROUP], principal_ns="acme")
    # Never public: no Linear issue is visible to every workspace principal.
    assert acl.public is False


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_regress_2026_07_05_open_team_denies_guest(respx_mock: respx.MockRouter) -> None:
    # Regression for the p5 over-share leak: an OPEN (non-private) team used to map to
    # AccessControl(public=True), which can_see resolves to True for EVERY principal in
    # the instance namespace. Linear GUESTS are workspace principals (paid seats that
    # appear in member management, https://linear.app/docs/members-roles) but per the
    # guest-accounts changelog (2022-07-14) a guest "will not be able to see or discover
    # any other teams in the workspace or have access to those issues." So the old
    # mapping leaked open-team issues to guests. This test constructs that exact
    # scenario and asserts the guest is now DENIED, while a full member (materialized
    # into the full-members group) is allowed. If someone reverts _acl to public=True,
    # this test fails, so the leak cannot silently return.
    open_issue = {
        "id": "i2",
        "title": "B",
        "description": "d",
        "team": {"id": "TEAM2", "key": "OPS", "private": False},
    }
    respx_mock.post(path="/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"issue": open_issue}})
    )
    conn = _factory()
    acl = await conn.list_acls(conn._ref("i2"))

    # A guest is a workspace principal, but is NOT in the full-members group (the
    # identity layer materializes that group with full members only, excluding guests).
    guest = Principal(user_id="guest1", groups=[])
    assert can_see(acl, guest) is False, "open-team issue must not leak to a Linear guest"

    # A full member, once the identity layer materializes the group, is allowed. This
    # is the intended (possibly under-sharing until materialized) grant, never public.
    full_member = Principal(user_id="member1", groups=[_WORKSPACE_MEMBERS_GROUP])
    assert can_see(acl, full_member) is True

    # And it is never public, which is what made the leak reachable for every principal.
    assert acl.public is False


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_regress_2026_07_06_null_issue_fails_closed(respx_mock: respx.MockRouter) -> None:
    # Regression for the residual fail-open on the privacy axis found in re-verify: a
    # GraphQL query for an inaccessible/deleted issue returns data.issue=null under HTTP
    # 200 (not an HTTP error), so _issue yielded {} and _acl({}) fell into the open-team
    # branch, granting the full-members group. A PRIVATE-team or unreadable issue would
    # thereby be exposed to every full member. The fix fails closed: no resolvable team
    # id means grant no one.
    respx_mock.post(path="/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"issue": None}})
    )
    conn = _factory()
    acl = await conn.list_acls(conn._ref("gone"))

    assert acl.allow_groups == [], "an unresolvable issue must grant no one"
    assert acl.allow_users == []
    assert acl.public is False
    member = Principal(user_id="member1", groups=[_WORKSPACE_MEMBERS_GROUP])
    assert can_see(acl, member) is False, "a null issue must not leak to full members"

    # A present team with the private flag ABSENT defaults to private (fail closed on the
    # privacy axis): only the team group, never the open-team members group.
    direct = conn._acl({"id": "i9", "team": {"id": "TEAM9"}})
    assert direct.allow_groups == ["TEAM9"]
    assert _WORKSPACE_MEMBERS_GROUP not in direct.allow_groups
