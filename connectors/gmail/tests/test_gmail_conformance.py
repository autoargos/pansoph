"""Hermetic conformance for the gmail connector, driven by respx mocks.

No live credentials. The Gmail HTTP surface the connector touches is mocked with
representative payloads:

  - GET /gmail/v1/users/me/profile   -> the current historyId (the durable cursor).
  - GET /gmail/v1/users/me/messages  -> the full-pass message listing.
  - GET /gmail/v1/users/me/history   -> the partial-sync change feed (messagesAdded and
                                        messagesDeleted), keyed by startHistoryId.
  - GET /gmail/v1/users/me/messages/{id} -> a single message for fetch.

assert_conforms drives the full contract: a full pass yields non-empty cursors, fetch
returns a RawDoc plus AccessControl, list_acls answers (permission_sync), and because
incremental is declared, replaying with the last cursor must not re-surface an already
synced live document. The permission category asserts the owner-only ACL against the
fixtures. Dedicated tests pin the ACL mapping, the incremental replay, and the
deletion tombstone.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_gmail.connector import GmailConnector
from pansoph_connector_sdk import assert_conforms
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_connector_sdk.verify.categories.sync import check_deletions, check_incremental
from pansoph_types import DocRef

_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> GmailConnector:
    return GmailConnector(
        instance_id="alice",
        config={"token": "x", "user_email": "alice@x.com"},
    )


def _seed_full_pass(respx_mock: respx.MockRouter, history_id: str = "1000") -> None:
    """Seed getProfile (cursor source) and a single-page messages.list full pass."""
    profile = {"emailAddress": "alice@x.com", "historyId": history_id}
    respx_mock.get(path="/gmail/v1/users/me/profile").mock(
        return_value=httpx.Response(200, json=profile)
    )
    respx_mock.get(path="/gmail/v1/users/me/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "m1", "threadId": "t1"}]})
    )


def _seed_history(
    respx_mock: respx.MockRouter,
    *,
    added: list[str] | None = None,
    deleted: list[str] | None = None,
    history_id: str = "1005",
) -> None:
    """Seed users.history.list with the given added and deleted message ids."""
    records: list[dict[str, object]] = []
    if added:
        records.append({"messagesAdded": [{"message": {"id": mid}} for mid in added]})
    if deleted:
        records.append({"messagesDeleted": [{"message": {"id": mid}} for mid in deleted]})
    respx_mock.get(path="/gmail/v1/users/me/history").mock(
        return_value=httpx.Response(200, json={"history": records, "historyId": history_id})
    )


def _seed_message(respx_mock: respx.MockRouter, message_id: str = "m1") -> None:
    respx_mock.get(path=f"/gmail/v1/users/me/messages/{message_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": message_id,
                "snippet": "hello world",
                "payload": {"headers": [{"name": "Subject", "value": "Greetings"}]},
            },
        )
    )


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_gmail_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    _seed_full_pass(respx_mock)
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["m1"]
    # Every ref on the full pass carries the mailbox historyId as its durable cursor.
    async for _ref, cursor in conn.list_changes(None):
        assert cursor == "1000"


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_gmail_assert_conforms(respx_mock: respx.MockRouter) -> None:
    # Full pass yields m1 at cursor 1000. The incremental replay (assert_conforms uses
    # the last cursor) must not re-surface m1: history reports only a new message m2 and
    # a permanently deleted m3, neither already synced.
    _seed_full_pass(respx_mock)
    _seed_message(respx_mock, "m1")
    _seed_history(respx_mock, added=["m2"], deleted=["m3"])
    conn = _factory()
    await assert_conforms(conn)


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_gmail_incremental_replay_no_leak(respx_mock: respx.MockRouter) -> None:
    _seed_full_pass(respx_mock)
    # The replay surfaces a brand-new message only, never an already-synced id.
    _seed_history(respx_mock, added=["m2"])
    conn = _factory()
    await check_incremental(conn, abnormal_state=None)


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_gmail_deletion_tombstone(respx_mock: respx.MockRouter) -> None:
    # Listing from a recorded cursor surfaces a permanent deletion as a tombstone.
    _seed_history(respx_mock, deleted=["m9"])
    conn = _factory()
    await check_deletions(conn, deleted_state="900")
    # Assert the tombstone shape directly: deleted=True, id preserved, instance carried.
    seen: list[DocRef] = []
    async for ref, cursor in conn.list_changes("900"):
        assert cursor
        seen.append(ref)
    assert [(r.source_doc_id, r.deleted, r.instance_id) for r in seen] == [("m9", True, "alice")]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_gmail_expired_cursor_falls_back_to_full(respx_mock: respx.MockRouter) -> None:
    # An expired startHistoryId returns HTTP 404 (outside the available history range).
    # The connector must downgrade to a full pass rather than raise.
    respx_mock.get(path="/gmail/v1/users/me/history").mock(return_value=httpx.Response(404))
    _seed_full_pass(respx_mock)
    conn = _factory()
    refs = [ref async for ref, _cursor in conn.list_changes("1")]
    assert [r.source_doc_id for r in refs] == ["m1"]
    assert all(not r.deleted for r in refs)


@pytest.mark.conformance
async def test_gmail_acl_owner_only() -> None:
    # The ACL for any message is exactly the mailbox owner: not public, no groups, no
    # deny, namespaced by the instance. No HTTP: the mapping is fully config-determined
    # for the gmail.readonly scope.
    conn = _factory()
    acl = await conn.list_acls(DocRef(source_doc_id="m1", instance_id="alice"))
    assert acl.allow_users == ["alice@x.com"]
    assert acl.public is False
    assert acl.allow_groups == []
    assert acl.deny == []
    assert acl.principal_ns == "alice"


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_gmail_permissions(respx_mock: respx.MockRouter) -> None:
    conn = _factory()
    await check_permissions(
        conn,
        source_acl=_FIX / "principals.json",
        principals=_FIX / "principals.json",
        expected=_FIX / "expected.json",
    )
