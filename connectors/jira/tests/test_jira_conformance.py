"""Hermetic conformance for the jira connector, driven by respx mocks.

No live credentials: every Jira REST call the connector makes is mocked with a
representative payload. Coverage:

- the full shared assert_conforms suite (contract, fetch/ACL types, list_acls),
- the corrected ACL: the browse audience is resolved from the project's permission
  scheme (BROWSE_PROJECTS grants, including a project-role holder), never a hardcoded
  role id, and an "any logged in user" grant (applicationRole / anyone / loggedin) fails
  closed to the reserved licensed-users membership group, NEVER to public (the over-share
  regression, see test_jira_regression_2026_07_05_application_role_not_public),
- issue security levels: a restricted issue maps to its level's members ONLY and never
  inherits the wider project audience, an unresolvable level fails closed (grants no
  one), and a present-but-empty security object {} is treated as restricted (fail
  closed), never as the browse audience, rather than over-sharing,
- deletions via reconciliation and the shared check_deletions category,
- the webhook translation of jira:issue_deleted and jira:issue_updated.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_jira.connector import JiraConnector
from pansoph_connector_sdk.conformance import assert_conforms
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_connector_sdk.verify.categories.sync import check_deletions
from pansoph_types import DocRef

_FIX = Path(__file__).parent / "fixtures" / "permissions"
_CONFIG = {
    "base_url": "https://acme.atlassian.net",
    "email": "e@x.com",
    "api_token": "t",
    "project_key": "ENG",
}

# The project permission scheme: BROWSE_PROJECTS is granted to a group and to a project
# role (10002). An unrelated permission and grant prove only browse grants are read.
_SCHEME = {
    "permissions": [
        {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "parameter": "jira-users"}},
        {"permission": "BROWSE_PROJECTS", "holder": {"type": "projectRole", "parameter": "10002"}},
        {"permission": "ADMINISTER_PROJECTS", "holder": {"type": "group", "parameter": "admins"}},
    ]
}
# Role 10002 resolves to user alice, so the faithful browse audience is
# groups {jira-users}, users {alice}.
_ROLE = {"actors": [{"actorUser": {"accountId": "alice"}}]}


def _factory() -> JiraConnector:
    return JiraConnector(instance_id="acme", config=_CONFIG)


def _mock_search_one_page(respx_mock: respx.MockRouter, keys: list[str]) -> None:
    respx_mock.post(path="/rest/api/3/search/jql").mock(
        return_value=httpx.Response(
            200, json={"issues": [{"key": k} for k in keys], "isLast": True}
        )
    )


def _mock_browse(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/rest/api/3/project/ENG/permissionscheme").mock(
        return_value=httpx.Response(200, json=_SCHEME)
    )
    respx_mock.get(path="/rest/api/3/project/ENG/role/10002").mock(
        return_value=httpx.Response(200, json=_ROLE)
    )


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_jira_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    _mock_search_one_page(respx_mock, ["ENG-1"])
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["ENG-1"]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_jira_list_changes_yields_non_empty_cursor(respx_mock: respx.MockRouter) -> None:
    # Regression for the audit: the final page previously yielded an empty cursor, which
    # fails assert_conforms. Every yielded cursor must now be non-empty.
    _mock_search_one_page(respx_mock, ["ENG-1", "ENG-2"])
    conn = _factory()
    cursors = [cursor async for _ref, cursor in conn.list_changes(None)]
    assert cursors and all(cursors), "every yielded cursor must be non-empty"


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_jira_assert_conforms(respx_mock: respx.MockRouter) -> None:
    # The full shared suite: contract, fetch/ACL types, and list_acls (permission_sync).
    # The seed issue is unrestricted (security null), so its ACL is the project audience.
    _mock_search_one_page(respx_mock, ["ENG-1"])
    respx_mock.get(url__regex=r".*/rest/api/3/issue/.*").mock(
        return_value=httpx.Response(
            200, json={"fields": {"summary": "s", "description": None, "security": None}}
        )
    )
    _mock_browse(respx_mock)
    conn = _factory()
    await assert_conforms(conn)


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_jira_browse_from_permission_scheme_not_role_id(respx_mock: respx.MockRouter) -> None:
    # The browse audience is the union of every BROWSE_PROJECTS holder: the group plus
    # the project role's actors. It is NOT read from a hardcoded role id.
    respx_mock.get(url__regex=r".*/rest/api/3/issue/.*").mock(
        return_value=httpx.Response(
            200, json={"fields": {"summary": "s", "description": None, "security": None}}
        )
    )
    _mock_browse(respx_mock)
    conn = _factory()
    _, acl = await conn.fetch(DocRef(source_doc_id="ENG-1", instance_id="acme"))
    assert acl.allow_groups == ["jira-users"]
    assert acl.allow_users == ["alice"]
    assert acl.public is False
    assert acl.principal_ns == "acme"


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_jira_regression_2026_07_05_application_role_not_public(
    respx_mock: respx.MockRouter,
) -> None:
    # REGRESSION (over-share leak, adversarial verdict 2026-07-05): a BROWSE_PROJECTS
    # grant to "any logged in user" (holder type applicationRole / anyone / loggedin) was
    # mapped to public=True. public becomes the tenant-everyone group, which includes
    # pansoph accounts with NO Atlassian license (they cannot browse the issue in Jira),
    # so the mapping over-shared. "Any logged in user" is every principal with a product
    # license on this site, strictly narrower than the whole tenant. It must now fail
    # closed to the reserved licensed-users membership group, NEVER public. The
    # unmaterialized group grants no one until the identity layer resolves it (safe
    # under-share). This test pins the non-leak so it cannot silently return, over every
    # holder-type spelling that means "any logged in user".
    scheme_route = respx_mock.get(path="/rest/api/3/project/ENG/permissionscheme")
    for holder_type in ("applicationRole", "anyone", "loggedin"):
        scheme_route.mock(
            return_value=httpx.Response(
                200,
                json={
                    "permissions": [
                        {
                            "permission": "BROWSE_PROJECTS",
                            "holder": {"type": holder_type, "parameter": "jira-software"},
                        }
                    ]
                },
            )
        )
        conn = _factory()
        acl = await conn._browse_acl(conn._client())
        assert acl.public is False, (
            f"holder {holder_type}: 'any logged in user' must NEVER map to public"
        )
        assert acl.allow_users == [], f"holder {holder_type}: no unlicensed tenant user granted"
        # It under-shares to the reserved membership group the identity layer must
        # materialize; until then that group has no members and grants no one.
        assert acl.allow_groups == ["__jira_licensed_users"], f"holder {holder_type}"


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_jira_regression_2026_07_05_empty_security_is_restricted(
    respx_mock: respx.MockRouter,
) -> None:
    # REGRESSION (over-share edge, adversarial verdict 2026-07-05): a present-but-empty
    # security object {} was falsy, so `if security:` fell through to the wider project
    # browse audience. A malformed/empty security level must instead be treated as
    # RESTRICTED and fail closed (grant no one), never inherit browse. No level/member
    # call is even attempted because there is no id.
    scheme = respx_mock.get(path="/rest/api/3/project/ENG/permissionscheme").mock(
        return_value=httpx.Response(200, json=_SCHEME)
    )
    respx_mock.get(url__regex=r".*/rest/api/3/issue/ENG-42.*").mock(
        return_value=httpx.Response(200, json={"fields": {"security": {}}})
    )
    conn = _factory()
    acl = await conn.list_acls(DocRef(source_doc_id="ENG-42", instance_id="acme"))
    assert acl.public is False
    assert acl.allow_users == [] and acl.allow_groups == []
    assert scheme.call_count == 0, "an empty security object must not fall back to browse"


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_jira_browse_fails_closed_when_scheme_unreadable(
    respx_mock: respx.MockRouter,
) -> None:
    # If the credential cannot read the project's permission scheme, the browse audience
    # fails closed (grants no one) rather than guessing a broad audience.
    respx_mock.get(path="/rest/api/3/project/ENG/permissionscheme").mock(
        return_value=httpx.Response(403, json={"errorMessages": ["forbidden"]})
    )
    conn = _factory()
    acl = await conn._browse_acl(conn._client())
    assert acl.public is False
    assert acl.allow_groups == [] and acl.allow_users == []


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_jira_browse_fetched_once_across_issues(respx_mock: respx.MockRouter) -> None:
    # The browse audience is project level, so it is resolved once and reused: fetching
    # it per issue would double the call count and risk the Atlassian rate limit.
    scheme = respx_mock.get(path="/rest/api/3/project/ENG/permissionscheme").mock(
        return_value=httpx.Response(200, json=_SCHEME)
    )
    respx_mock.get(path="/rest/api/3/project/ENG/role/10002").mock(
        return_value=httpx.Response(200, json=_ROLE)
    )
    respx_mock.get(url__regex=r".*/rest/api/3/issue/.*").mock(
        return_value=httpx.Response(
            200, json={"fields": {"summary": "s", "description": None, "security": None}}
        )
    )
    conn = _factory()
    for key in ("ENG-1", "ENG-2", "ENG-3"):
        _, acl = await conn.fetch(DocRef(source_doc_id=key, instance_id="acme"))
        assert acl.allow_groups == ["jira-users"]
    assert scheme.call_count == 1


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_jira_security_level_restricts_not_project(respx_mock: respx.MockRouter) -> None:
    # The headline security fix: a security-restricted issue maps to its level's members
    # ONLY, never the wider project browse audience. A project-only member (alice) must
    # NOT be able to see it; only the level members (carol, security-team) may.
    respx_mock.get(url__regex=r".*/rest/api/3/issue/ENG-9.*").mock(
        return_value=httpx.Response(
            200, json={"fields": {"security": {"id": "10100", "name": "Confidential"}}}
        )
    )
    respx_mock.get(
        path="/rest/api/3/issuesecurityschemes/level/member",
        params__contains={"levelId": "10100"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    {"holder": {"type": "user", "parameter": "carol"}},
                    {"holder": {"type": "group", "parameter": "security-team"}},
                ],
                "isLast": True,
                "startAt": 0,
            },
        )
    )
    conn = _factory()
    acl = await conn.list_acls(DocRef(source_doc_id="ENG-9", instance_id="acme"))
    assert acl.allow_users == ["carol"]
    assert acl.allow_groups == ["security-team"]
    assert acl.public is False
    assert "jira-users" not in acl.allow_groups, "a restricted issue must not inherit browse"
    assert "alice" not in acl.allow_users, "a project-only member must not see a restricted issue"


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_jira_security_level_fails_closed(respx_mock: respx.MockRouter) -> None:
    # Resolving level members needs Administer Jira global permission, which read
    # credentials lack (403). The issue must fail closed (grant no one), never fall back
    # to the project audience.
    respx_mock.get(url__regex=r".*/rest/api/3/issue/ENG-13.*").mock(
        return_value=httpx.Response(
            200, json={"fields": {"security": {"id": "10200", "name": "Locked"}}}
        )
    )
    respx_mock.get(
        path="/rest/api/3/issuesecurityschemes/level/member",
        params__contains={"levelId": "10200"},
    ).mock(return_value=httpx.Response(403, json={"errorMessages": ["Administer Jira required"]}))
    conn = _factory()
    acl = await conn.list_acls(DocRef(source_doc_id="ENG-13", instance_id="acme"))
    assert acl.public is False
    assert acl.allow_users == [] and acl.allow_groups == []


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_jira_deletions_reconcile(respx_mock: respx.MockRouter) -> None:
    # Run one lists ENG-1 and ENG-2. Run two lists only ENG-1, so ENG-2 (deleted or now
    # hidden by a security level) must reconcile as a tombstone and only it.
    _mock_search_one_page(respx_mock, ["ENG-1", "ENG-2"])
    conn = _factory()
    first_cursor: str | None = None
    async for _ref, cursor in conn.list_changes(None):
        first_cursor = cursor
    assert first_cursor

    respx_mock.reset()
    _mock_search_one_page(respx_mock, ["ENG-1"])
    conn2 = _factory()
    seen: dict[str, bool] = {}
    async for ref, _cursor in conn2.list_changes(first_cursor):
        seen[ref.source_doc_id] = ref.deleted
    assert seen == {"ENG-1": False, "ENG-2": True}, "ENG-2 must reconcile as a deletion tombstone"


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_jira_check_deletions_contract(respx_mock: respx.MockRouter) -> None:
    # The shared deletions category: listing from a cursor recorded before a removal must
    # surface at least one deleted DocRef. The deleted_state encodes {ENG-1, ENG-2}; the
    # current listing has only ENG-1, so ENG-2 reconciles as a tombstone.
    _mock_search_one_page(respx_mock, ["ENG-1"])
    conn = _factory()
    deleted_state = JiraConnector._encode_cursor({"ENG-1", "ENG-2"})
    await check_deletions(conn, deleted_state)


@pytest.mark.conformance
async def test_jira_webhook_delete_and_upsert() -> None:
    conn = _factory()
    deleted = await conn.on_webhook(
        {"webhookEvent": "jira:issue_deleted", "issue": {"key": "ENG-7"}}
    )
    assert len(deleted) == 1
    assert deleted[0].kind == "delete" and deleted[0].source_doc_id == "ENG-7"

    updated = await conn.on_webhook(
        {"webhookEvent": "jira:issue_updated", "issue": {"key": "ENG-8"}}
    )
    assert len(updated) == 1
    assert updated[0].kind == "upsert" and updated[0].source_doc_id == "ENG-8"

    # An unrelated event, or one without an issue key, yields nothing.
    assert await conn.on_webhook({"webhookEvent": "comment_created"}) == []
    assert await conn.on_webhook({"webhookEvent": "jira:issue_deleted", "issue": {}}) == []


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_jira_permissions(respx_mock: respx.MockRouter) -> None:
    # The gated permission category over three shapes: an unrestricted issue (project
    # audience), a security-restricted issue (level members only), and an unresolvable
    # restricted issue (fail closed). Set equality plus per-principal visibility both
    # assert no over-share.
    # Anchor on the "?" so the ENG-1 route does not also match ENG-13.
    respx_mock.get(url__regex=r".*/rest/api/3/issue/ENG-1\?.*").mock(
        return_value=httpx.Response(200, json={"fields": {"security": None}})
    )
    respx_mock.get(url__regex=r".*/rest/api/3/issue/ENG-9\?.*").mock(
        return_value=httpx.Response(
            200, json={"fields": {"security": {"id": "10100", "name": "Confidential"}}}
        )
    )
    respx_mock.get(url__regex=r".*/rest/api/3/issue/ENG-13\?.*").mock(
        return_value=httpx.Response(
            200, json={"fields": {"security": {"id": "10200", "name": "Locked"}}}
        )
    )
    _mock_browse(respx_mock)
    respx_mock.get(
        path="/rest/api/3/issuesecurityschemes/level/member",
        params__contains={"levelId": "10100"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "values": [
                    {"holder": {"type": "user", "parameter": "carol"}},
                    {"holder": {"type": "group", "parameter": "security-team"}},
                ],
                "isLast": True,
                "startAt": 0,
            },
        )
    )
    respx_mock.get(
        path="/rest/api/3/issuesecurityschemes/level/member",
        params__contains={"levelId": "10200"},
    ).mock(return_value=httpx.Response(403, json={"errorMessages": ["Administer Jira required"]}))
    conn = _factory()
    await check_permissions(
        conn,
        source_acl=_FIX / "principals.json",
        principals=_FIX / "principals.json",
        expected=_FIX / "expected.json",
    )
