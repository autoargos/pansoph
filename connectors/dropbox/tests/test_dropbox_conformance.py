"""Hermetic conformance for the dropbox connector, driven by respx mocks.

No live credentials: every Dropbox HTTP API v2 call the connector makes is mocked
with a payload shaped like the real response (verified 2026-07-05 against the Python
SDK reference and the Detecting Changes guide). Dropbox uses POST with JSON bodies,
so each route is registered on respx_mock.post by path.

The suite exercises the full shared conformance contract (assert_conforms), the
incremental and deletions categories (the connector declares both), and the gated
permission category, which pins the ACL mapping to the documented member schema
(access_type-aware, invitees excluded).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_dropbox.connector import DropboxConnector
from pansoph_connector_sdk.conformance import assert_conforms
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_connector_sdk.verify.categories.sync import check_deletions, check_incremental

_FIX = Path(__file__).parent / "fixtures" / "permissions"

# The go-forward cursor get_latest_cursor hands back after the initial full walk. It
# is the durable resume point every first-pass DocRef is paired with.
_FORWARD_CURSOR = "cur:forward"


def _factory() -> DropboxConnector:
    return DropboxConnector(instance_id="dbx", config={"token": "x"})


def _mock_initial_listing(respx_mock: respx.MockRouter) -> None:
    """One live file, no more pages, then the go-forward cursor.

    FileMetadata entries carry both id and path_lower; the connector keys on
    path_lower because that is the identifier a later DeletedMetadata also carries.
    """
    respx_mock.post(path="/2/files/list_folder").mock(
        return_value=httpx.Response(
            200,
            json={
                "entries": [
                    {".tag": "file", "id": "id:F1", "name": "a.txt", "path_lower": "/a.txt"}
                ],
                "has_more": False,
                "cursor": "cur:page",
            },
        )
    )
    respx_mock.post(path="/2/files/list_folder/get_latest_cursor").mock(
        return_value=httpx.Response(200, json={"cursor": _FORWARD_CURSOR})
    )


def _mock_members(respx_mock: respx.MockRouter) -> None:
    """sharing/list_file_members in the documented SharedFileMembers shape.

    alice is a viewer (reads), eng is an editor group (reads). mallory has traverse
    only (navigation, not read) and must not appear. pending@x.com is an invitee (not
    yet a member) and must not appear. No cursor, so a single page.
    """
    respx_mock.post(path="/2/sharing/list_file_members").mock(
        return_value=httpx.Response(
            200,
            json={
                "users": [
                    {
                        "access_type": {".tag": "viewer"},
                        "user": {
                            "account_id": "dbid:alice",
                            "email": "alice@x.com",
                            "display_name": "Alice",
                            "same_team": False,
                        },
                        "is_inherited": False,
                    },
                    {
                        "access_type": {".tag": "traverse"},
                        "user": {
                            "account_id": "dbid:mallory",
                            "email": "mallory@x.com",
                            "display_name": "Mallory",
                            "same_team": False,
                        },
                        "is_inherited": False,
                    },
                ],
                "groups": [
                    {
                        "access_type": {".tag": "editor"},
                        "group": {
                            "group_name": "eng",
                            "group_id": "g:eng",
                            "member_count": 3,
                        },
                    }
                ],
                "invitees": [
                    {
                        "access_type": {".tag": "viewer"},
                        "invitee": {".tag": "email", "email": "pending@x.com"},
                    }
                ],
            },
        )
    )


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_dropbox_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    _mock_initial_listing(respx_mock)
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["/a.txt"]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_dropbox_paginates_all_pages(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(path="/2/files/list_folder").mock(
        return_value=httpx.Response(
            200,
            json={
                "entries": [{".tag": "file", "id": "F1", "name": "a", "path_lower": "/a"}],
                "has_more": True,
                "cursor": "C1",
            },
        )
    )
    respx_mock.post(path="/2/files/list_folder/continue").mock(
        return_value=httpx.Response(
            200,
            json={
                "entries": [{".tag": "file", "id": "F2", "name": "b", "path_lower": "/b"}],
                "has_more": False,
                "cursor": "C2",
            },
        )
    )
    respx_mock.post(path="/2/files/list_folder/get_latest_cursor").mock(
        return_value=httpx.Response(200, json={"cursor": _FORWARD_CURSOR})
    )
    conn = _factory()
    refs = [ref async for ref, _ in conn.list_changes(None)]
    assert [r.source_doc_id for r in refs] == ["/a", "/b"]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_dropbox_first_pass_yields_forward_cursor(respx_mock: respx.MockRouter) -> None:
    """The first pass pairs every ref with the durable go-forward cursor."""
    _mock_initial_listing(respx_mock)
    conn = _factory()
    cursors = [cur async for _, cur in conn.list_changes(None)]
    assert cursors == [_FORWARD_CURSOR]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_dropbox_incremental_emits_deletion(respx_mock: respx.MockRouter) -> None:
    """A stored cursor replayed through continue surfaces a DeletedMetadata tombstone.

    DeletedMetadata carries path_lower but no id, so the connector keys the tombstone
    on path_lower. This is the exact behavior the deletions capability claims.
    """
    respx_mock.post(path="/2/files/list_folder/continue").mock(
        return_value=httpx.Response(
            200,
            json={
                "entries": [
                    {".tag": "deleted", "name": "gone.txt", "path_lower": "/gone.txt"},
                    {".tag": "file", "id": "id:F9", "name": "new.txt", "path_lower": "/new.txt"},
                ],
                "has_more": False,
                "cursor": "cur:next",
            },
        )
    )
    conn = _factory()
    changes = [
        (ref.source_doc_id, ref.deleted, cur) async for ref, cur in conn.list_changes("cur:stored")
    ]
    assert ("/gone.txt", True, "cur:next") in changes
    assert ("/new.txt", False, "cur:next") in changes


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_dropbox_conforms(respx_mock: respx.MockRouter) -> None:
    """The full shared conformance contract, including incremental and deletions.

    assert_conforms seeds from the initial listing (get_metadata + list_file_members
    back the fetch and list_acls steps), then check_incremental and check_deletions
    exercise the incremental cursor and the tombstone the connector now truly emits.
    """
    _mock_initial_listing(respx_mock)
    _mock_members(respx_mock)
    respx_mock.post(path="/2/files/get_metadata").mock(
        return_value=httpx.Response(200, json={"name": "a.txt", "path_lower": "/a.txt"})
    )
    # Incremental replay: an empty change set that still advances the cursor.
    incremental = respx_mock.post(path="/2/files/list_folder/continue").mock(
        return_value=httpx.Response(
            200, json={"entries": [], "has_more": False, "cursor": "cur:next"}
        )
    )

    conn = _factory()
    await assert_conforms(conn)
    await check_incremental(conn, abnormal_state=None)

    # deletions: from a stored cursor, continue reports the tombstone.
    incremental.mock(
        return_value=httpx.Response(
            200,
            json={
                "entries": [{".tag": "deleted", "name": "gone", "path_lower": "/gone"}],
                "has_more": False,
                "cursor": "cur:after-delete",
            },
        )
    )
    await check_deletions(conn, deleted_state="cur:before-delete")


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_dropbox_permissions(respx_mock: respx.MockRouter) -> None:
    _mock_members(respx_mock)
    conn = _factory()
    await check_permissions(
        conn,
        source_acl=_FIX / "principals.json",
        principals=_FIX / "principals.json",
        expected=_FIX / "expected.json",
    )
