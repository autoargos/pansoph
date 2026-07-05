"""Hermetic conformance for the slack connector, driven by respx mocks.

No live credentials: every Slack Web API call the connector makes is mocked with a
representative payload. The permission category asserts a private channel ACL maps to
its member user ids, with no groups and no deny (Slack exposes neither per channel).

The shared assert_conforms harness is wired here (test_slack_assert_conforms), so the
connector is gated by the same contract every other connector is, not only by the
weaker per-category checks. Two regression tests pin the security fixes: an
ok=false conversations.info must never produce a public grant (the ACL leak), and an
ok=false conversations.members must raise rather than persist an empty member set (the
silent under-share). Both are grounded in the Slack ok-envelope: failures arrive as
HTTP 200 with {"ok": false, "error": "..."} (docs.slack.dev/apis/web-api/, verified
2026-07-05).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_sdk import assert_conforms
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_connector_slack.connector import SlackApiError, SlackConnector

_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> SlackConnector:
    return SlackConnector(
        instance_id="acme",
        config={"token": "xoxb-test", "base_url": "https://slack.com"},
    )


def _mock_list(respx_mock: respx.MockRouter) -> None:
    """A single conversations.list page with one public and one private channel."""
    respx_mock.get(path="/api/conversations.list").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "channels": [
                    {"id": "C1", "name": "general", "is_private": False},
                    {"id": "C2", "name": "secret-plans", "is_private": True},
                ],
                "response_metadata": {"next_cursor": ""},
            },
        )
    )


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_slack_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    _mock_list(respx_mock)
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["C1", "C2"]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_slack_list_changes_yields_non_empty_cursor(respx_mock: respx.MockRouter) -> None:
    # The shared contract (conformance.py) requires every yielded cursor to be a
    # non-empty string. Assert it directly on the last page (empty Slack next_cursor)
    # to lock in that list_changes never yields "".
    _mock_list(respx_mock)
    conn = _factory()
    cursors = [cursor async for _ref, cursor in conn.list_changes(None)]
    assert cursors and all(cursor for cursor in cursors)


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_slack_assert_conforms(respx_mock: respx.MockRouter) -> None:
    # Drive the single shared conformance harness end to end: discovery, fetch (which
    # needs conversations.history plus the ACL calls), list_acls, and resource
    # discovery. incremental and deletions are False, so assert_conforms does not
    # exercise those paths; the honest flags are asserted separately below.
    _mock_list(respx_mock)
    respx_mock.get(path="/api/conversations.history").mock(
        return_value=httpx.Response(200, json={"ok": True, "messages": [{"text": "hello"}]})
    )
    respx_mock.get(path="/api/conversations.info").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "channel": {"id": "C1", "is_private": False}}
        )
    )
    conn = _factory()
    await assert_conforms(conn)


@pytest.mark.conformance
async def test_slack_capability_flags_are_honest() -> None:
    # incremental and deletions are declared False because the implementation does
    # not resume from a since-token and does not emit tombstones. permission_sync is
    # True and backed by list_acls. This pins the honesty claim so a future True flag
    # that does nothing fails here.
    caps = _factory().meta.capabilities
    assert caps.incremental is False
    assert caps.deletions is False
    assert caps.permission_sync is True
    assert caps.resource_discovery is True


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_slack_paginates_all_pages(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/api/conversations.list").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "ok": True,
                    "channels": [{"id": "C1", "is_private": False}],
                    "response_metadata": {"next_cursor": "NC"},
                },
            ),
            httpx.Response(
                200,
                json={
                    "ok": True,
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
                    "ok": True,
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
                    "ok": True,
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
        return_value=httpx.Response(
            200, json={"ok": True, "channel": {"id": "C2", "is_private": True}}
        )
    )
    respx_mock.get(path="/api/conversations.members").mock(
        return_value=httpx.Response(
            200,
            json={"ok": True, "members": ["U1", "U2"], "response_metadata": {"next_cursor": ""}},
        )
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
async def test_slack_info_ok_false_never_leaks_public(respx_mock: respx.MockRouter) -> None:
    # REGRESSION (critical ACL leak). Slack returns HTTP 200 with {"ok": false} for a
    # channel the credential cannot resolve (channel_not_found, missing_scope,
    # not_in_channel, invalid_auth, token_revoked). The old code read
    # info.json().get("channel", {}), saw is_private absent, and published
    # AccessControl(public=True). A private/unreadable document must NEVER become
    # world readable inside pansoph. The fix fails closed: _acl raises SlackApiError.
    respx_mock.get(path="/api/conversations.info").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
    )
    conn = _factory()
    with pytest.raises(SlackApiError) as exc:
        await conn.list_acls(conn._ref("C_SECRET"))
    assert exc.value.code == "channel_not_found"


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_slack_members_not_in_channel_raises(respx_mock: respx.MockRouter) -> None:
    # REGRESSION (silent under-share). A bot cannot self-join a private channel and
    # must be invited; conversations.members on a channel the app is not in returns
    # HTTP 200 {"ok": false, "error": "not_in_channel"}. The old code read
    # payload.get("members", []) == [] and persisted AccessControl(allow_users=[]),
    # a channel visible to nobody though real members exist, and indistinguishable
    # from a genuinely empty channel. The fix raises so the runtime does not persist a
    # false empty ACL.
    respx_mock.get(path="/api/conversations.info").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "channel": {"id": "C2", "is_private": True}}
        )
    )
    respx_mock.get(path="/api/conversations.members").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "not_in_channel"})
    )
    conn = _factory()
    with pytest.raises(SlackApiError) as exc:
        await conn.list_acls(conn._ref("C2"))
    assert exc.value.code == "not_in_channel"


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_slack_history_ok_false_raises(respx_mock: respx.MockRouter) -> None:
    # REGRESSION (silent empty ingest). conversations.history ok=false (missing
    # channels:history/groups:history, or not_in_channel) previously indexed an empty
    # document. The fix raises so the scope/membership failure surfaces.
    respx_mock.get(path="/api/conversations.history").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "missing_scope"})
    )
    conn = _factory()
    with pytest.raises(SlackApiError) as exc:
        await conn.fetch(conn._ref("C1"))
    assert exc.value.code == "missing_scope"
