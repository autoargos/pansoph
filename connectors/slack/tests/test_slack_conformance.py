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
@respx.mock(assert_all_called=False)
async def test_slack_paginates_all_pages(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/api/conversations.list").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "channels": [{"id": "C1", "is_private": False}],
                    "response_metadata": {"next_cursor": "NC"},
                },
            ),
            httpx.Response(
                200,
                json={
                    "channels": [{"id": "C2", "is_private": False}],
                    "response_metadata": {"next_cursor": ""},
                },
            ),
        ]
    )
    conn = _factory()
    ids = [ref.source_doc_id async for ref, _ in conn.list_changes(None)]
    assert ids == ["C1", "C2"]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_slack_list_resources_parses_and_paginates(respx_mock: respx.MockRouter) -> None:
    # Two Slack pages: page one carries a next_cursor, page two closes the listing
    # with an empty cursor. list_resources maps one Slack page to one ResourcePage,
    # so the caller drives pagination with the returned next_cursor.
    respx_mock.get(path="/api/conversations.list").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "channels": [
                        {"id": "C1", "name": "general", "is_private": False},
                        {"id": "C2", "name": "secret-plans", "is_private": True},
                    ],
                    "response_metadata": {"next_cursor": "dXNlcjpVMEc5V0ZYTlo="},
                },
            ),
            httpx.Response(
                200,
                json={
                    "channels": [{"id": "C3", "name": "random", "is_private": False}],
                    "response_metadata": {"next_cursor": ""},
                },
            ),
        ]
    )
    conn = _factory()

    first = await conn.list_resources()
    assert [r.id for r in first.items] == ["C1", "C2"]
    assert [r.kind for r in first.items] == ["channel", "channel"]
    assert [r.parent_id for r in first.items] == [None, None]
    assert all(r.selectable for r in first.items)
    # Human labels: hash for public, lock for private, name preserved.
    assert first.items[0].label == "# general"
    assert first.items[1].label.endswith("secret-plans")
    assert first.items[1].extra["is_private"] == "true"
    assert first.next_cursor == "dXNlcjpVMEc5V0ZYTlo="

    second = await conn.list_resources(cursor=first.next_cursor)
    assert [r.id for r in second.items] == ["C3"]
    # An empty Slack cursor means the listing is exhausted.
    assert second.next_cursor is None


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_slack_list_resources_unknown_kind_is_empty(respx_mock: respx.MockRouter) -> None:
    # A kind Slack does not model, or a non-null parent (there is no level below a
    # channel), lists nothing and makes no upstream call.
    route = respx_mock.get(path="/api/conversations.list")
    conn = _factory()

    for page in (
        await conn.list_resources(kind="folder"),
        await conn.list_resources(parent_id="C1"),
    ):
        assert page.items == []
        assert page.next_cursor is None
    assert not route.called


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
