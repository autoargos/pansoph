"""Hermetic conformance for the linear connector, driven by respx mocks.

No live credentials: every Linear GraphQL call the connector makes is mocked with a
representative payload. Both list_changes and fetch/list_acls hit the same /graphql
path, so each test mocks the single response its call under test expects. The
permission category asserts the best effort, team scoped ACL maps an issue's team to
allow_groups (no per issue viewers, no deny).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_linear.connector import LinearConnector
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions

_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> LinearConnector:
    return LinearConnector(instance_id="acme", config={"token": "k"})


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_linear_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(path="/graphql").mock(
        return_value=httpx.Response(
            200, json={"data": {"issues": {"nodes": [{"id": "i1", "title": "A"}]}}}
        )
    )
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["i1"]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_linear_paginates_all_pages(respx_mock: respx.MockRouter) -> None:
    page1 = httpx.Response(
        200,
        json={
            "data": {
                "issues": {
                    "nodes": [{"id": "i1", "title": "A"}],
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
                    "nodes": [{"id": "i2", "title": "B"}],
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
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_linear_permissions(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(path="/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "issue": {
                        "id": "i1",
                        "title": "A",
                        "description": "d",
                        "team": {"id": "TEAM1", "key": "ENG"},
                    }
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
