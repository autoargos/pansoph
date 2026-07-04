"""Hermetic conformance for the jira connector, driven by respx mocks.

No live credentials: every Jira REST call the connector makes is mocked with a
representative payload. Discovery exercises the token paginated POST /search/jql
endpoint (the old /search is 410 Gone). The permission category asserts the ACL
maps from the project Browse role actors: actorGroup to allow_groups and actorUser
accountId to allow_users.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_jira.connector import JiraConnector
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_types import DocRef

_FIX = Path(__file__).parent / "fixtures" / "permissions"
_CONFIG = {
    "base_url": "https://acme.atlassian.net",
    "email": "e@x.com",
    "api_token": "t",
    "project_key": "ENG",
}


def _factory() -> JiraConnector:
    return JiraConnector(instance_id="acme", config=_CONFIG)


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_jira_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(path="/rest/api/3/search/jql").mock(
        return_value=httpx.Response(200, json={"issues": [{"key": "ENG-1"}], "isLast": True})
    )
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["ENG-1"]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_jira_acl_fetched_once_across_issues(respx_mock: respx.MockRouter) -> None:
    # Regression: the ACL is project level (the project Browse role), so it is constant
    # for every issue in a sync. Fetching it per issue doubled the call count and risked
    # the Atlassian rate limit mid-sync. One project-role call must serve many fetches.
    role = respx_mock.get(path="/rest/api/3/project/ENG/role/10002").mock(
        return_value=httpx.Response(200, json={"actors": [{"actorGroup": {"name": "jira-users"}}]})
    )
    respx_mock.get(url__regex=r"/rest/api/3/issue/.*").mock(
        return_value=httpx.Response(200, json={"fields": {"summary": "s", "description": None}})
    )
    conn = _factory()
    for key in ("ENG-1", "ENG-2", "ENG-3"):
        _, acl = await conn.fetch(DocRef(source_doc_id=key, instance_id="acme"))
        assert acl.allow_groups == ["jira-users"]
    assert role.call_count == 1


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_jira_permissions(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/rest/api/3/project/ENG/role/10002").mock(
        return_value=httpx.Response(
            200,
            json={
                "actors": [
                    {"actorGroup": {"name": "jira-users"}},
                    {"actorUser": {"accountId": "alice"}},
                ]
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
