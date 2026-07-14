"""Hermetic conformance for the slack connector, driven by respx mocks.

No live credentials: every Slack Web API call the connector makes is mocked with a
representative payload. The permission category asserts a private channel ACL maps to
its member user ids, with no groups and no deny (Slack exposes neither per channel).

The shared assert_conforms harness is wired here (test_slack_assert_conforms), so the
connector is gated by the same contract every other connector is, not only by the
weaker per-category checks. Regression tests pin the security fixes:

  - test_slack_public_channel_never_maps_to_public: a public channel must NEVER map to
    public=True (the over-share leak), it maps to the namespaced full-member group,
    which under-shares until the identity layer materializes it. Grounded in the Slack
    guest-roles rules: guests must be explicitly added to a channel and cannot read
    arbitrary public channels (slack.com/help/articles/202518103, verified 2026-07-05).
  - test_slack_missing_is_private_fails_closed_to_private: a missing is_private on an
    ok=true conversations.info defaults to private (the narrower side), not public.
  - test_slack_info_ok_false_never_leaks_public: an ok=false conversations.info must
    raise, never produce a public grant.
  - test_slack_members_not_in_channel_raises: an ok=false conversations.members must
    raise rather than persist an empty member set (silent under-share).

The ok-envelope cases are grounded in the Slack contract: failures arrive as HTTP 200
with {"ok": false, "error": "..."} (docs.slack.dev/apis/web-api/, verified 2026-07-05).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_sdk import assert_conforms
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_connector_slack.connector import (
    _WORKSPACE_MEMBER_GROUP,
    SlackApiError,
    SlackConnector,
)

_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> SlackConnector:
    return SlackConnector(
        instance_id="acme",
        config={"token": "xoxb-test", "base_url": "https://slack.com"},
    )


def _factory_no_autojoin() -> SlackConnector:
    return SlackConnector(
        instance_id="acme",
        config={"token": "xoxb-test", "base_url": "https://slack.com", "auto_join": False},
    )


def _mock_list(respx_mock: respx.MockRouter) -> None:
    """A single conversations.list page with one public and one private channel, both of
    which the credential is a member of (is_member true), so both are ingested."""
    respx_mock.get(path="/api/conversations.list").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "channels": [
                    {"id": "C1", "name": "general", "is_private": False, "is_member": True},
                    {"id": "C2", "name": "secret-plans", "is_private": True, "is_member": True},
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
async def test_slack_list_changes_skips_channels_the_bot_is_not_in(
    respx_mock: respx.MockRouter,
) -> None:
    # A bot can only read the history of channels it was invited to. conversations.list
    # returns is_member for the calling token; listing a non-member channel would only make
    # fetch hit not_in_channel and abort the whole sync on a channel that is correctly
    # inaccessible. So list_changes ingests exactly the member channels.
    respx_mock.get(path="/api/conversations.list").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "channels": [
                    {"id": "C1", "name": "in", "is_private": False, "is_member": True},
                    {"id": "C2", "name": "not-in", "is_private": False, "is_member": False},
                    {"id": "C3", "name": "no-flag", "is_private": False},
                ],
                "response_metadata": {"next_cursor": ""},
            },
        )
    )
    # auto_join off: a non-member channel is purely skipped, no join attempted.
    conn = _factory_no_autojoin()
    ids = [ref.source_doc_id async for ref, _ in conn.list_changes(None)]
    assert ids == ["C1"], "only the channel the bot is a member of is ingested"


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_slack_auto_joins_public_channels_then_ingests(
    respx_mock: respx.MockRouter,
) -> None:
    # With auto_join on (the default), a public channel the bot is not in is joined first
    # (conversations.join), then ingested. A private non-member channel is never joined and
    # is skipped, and a public channel whose join fails (for example missing channels:join
    # scope) is skipped without aborting the sync.
    respx_mock.get(path="/api/conversations.list").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "channels": [
                    {"id": "CMEMBER", "is_private": False, "is_member": True},
                    {"id": "CJOINABLE", "is_private": False, "is_member": False},
                    {"id": "CPRIVATE", "is_private": True, "is_member": False},
                    {"id": "CNOSCOPE", "is_private": False, "is_member": False},
                ],
                "response_metadata": {"next_cursor": ""},
            },
        )
    )

    def _join(request: httpx.Request) -> httpx.Response:
        body = httpx.QueryParams(request.content.decode())
        if body.get("channel") == "CJOINABLE":
            return httpx.Response(200, json={"ok": True, "channel": {"id": "CJOINABLE"}})
        # CNOSCOPE cannot be joined (simulating missing channels:join scope).
        return httpx.Response(200, json={"ok": False, "error": "missing_scope"})

    respx_mock.post(path="/api/conversations.join").mock(side_effect=_join)

    conn = _factory()
    ids = [ref.source_doc_id async for ref, _ in conn.list_changes(None)]
    assert ids == ["CMEMBER", "CJOINABLE"], (
        "ingest the already-member channel and the joinable public one; skip the private "
        "and the un-joinable public channel"
    )


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
                    "channels": [{"id": "C1", "is_private": False, "is_member": True}],
                    "response_metadata": {"next_cursor": "NC"},
                },
            ),
            httpx.Response(
                200,
                json={
                    "ok": True,
                    "channels": [{"id": "C2", "is_private": False, "is_member": True}],
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
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_slack_public_channel_never_maps_to_public(respx_mock: respx.MockRouter) -> None:
    # REGRESSION (over-share leak, connector.py:409-410 in the audited version). The old
    # code mapped a public channel (is_private=False) to AccessControl(public=True),
    # which pansoph-authz translates into a reader grant to the tenant-everyone group,
    # i.e. EVERY principal in the tenant. But a Slack public channel is NOT world
    # readable: single/multi-channel GUESTS must be explicitly added to a channel and
    # cannot read arbitrary public channels (guest-roles help article, verified
    # 2026-07-05), and pansoph tenant-everyone can include principals with no Slack
    # membership at all. So a principal who cannot read the channel in Slack would get
    # read access in pansoph -> over-broad grant. The fix fails closed to the narrower
    # side: a public channel maps to the namespaced full-member group, which UNDER-shares
    # (grants no one until the identity layer materializes it) rather than over-shares.
    #
    # This pins the non-leak: public MUST be False (public is exactly the flag the authz
    # mapper keys off to grant tenant-everyone, so public=False denies the previously
    # leaked tenant-everyone/guest/non-Slack principal), the only grant is the
    # unmaterialized membership group, and there is no direct-user grant.
    respx_mock.get(path="/api/conversations.info").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "channel": {"id": "C1", "is_private": False}}
        )
    )
    conn = _factory()
    acl = await conn.list_acls(conn._ref("C1"))
    assert acl.public is False
    assert acl.allow_users == []
    # The grant is the full-member group, namespaced PER INSTANCE. The bare
    # "slack:member" would alias two Slack workspaces in one tenant onto one authz node
    # (the mapper renders a group in the tenant namespace), leaking workspace A's public
    # docs to workspace B's members once the group is materialized. The per-instance id
    # keeps them distinct.
    assert acl.allow_groups == [f"{_WORKSPACE_MEMBER_GROUP}:{conn.instance_id}"]
    assert acl.allow_groups[0].startswith("slack:member:")
    assert acl.allow_groups != [_WORKSPACE_MEMBER_GROUP]


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_slack_missing_is_private_fails_closed_to_private(
    respx_mock: respx.MockRouter,
) -> None:
    # REGRESSION (fail-open default). When is_private is absent on an ok=true
    # conversations.info, the old code (channel.get("is_private", False)) defaulted to
    # PUBLIC and emitted a public grant. Slack always returns is_private, but this was
    # the one path that failed open. The fix defaults to private (the narrower side), so
    # a missing field routes into the member-user path (conversations.members), never a
    # broad grant. Here members returns a concrete user set and public stays False.
    respx_mock.get(path="/api/conversations.info").mock(
        return_value=httpx.Response(200, json={"ok": True, "channel": {"id": "C9"}})
    )
    respx_mock.get(path="/api/conversations.members").mock(
        return_value=httpx.Response(
            200,
            json={"ok": True, "members": ["U7"], "response_metadata": {"next_cursor": ""}},
        )
    )
    conn = _factory()
    acl = await conn.list_acls(conn._ref("C9"))
    assert acl.public is False
    assert acl.allow_groups == []
    assert acl.allow_users == ["U7"]


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
