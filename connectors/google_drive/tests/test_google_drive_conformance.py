"""Hermetic conformance for the google_drive connector, driven by respx mocks.

No live OAuth token: the Drive v3 changes.getStartPageToken, changes.list, files.get,
and permissions.list calls are mocked with representative payloads. The full shared
assert_conforms suite runs so the incremental and deletions contract checks (which are
capability-gated on the flags this connector declares) actually execute, and the
permission category asserts the file permissions map to allow_users (user emails) and
allow_groups (group emails, domain grants namespaced "domain:<domain>").
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_google_drive.connector import GoogleDriveConnector
from pansoph_connector_sdk import assert_conforms
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions

_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> GoogleDriveConnector:
    return GoogleDriveConnector(instance_id="drive", config={"token": "x"})


def _seed_change_feed(respx_mock: respx.MockRouter) -> None:
    """Seed a two-poll change feed: the initial startPageToken, one live change (f1),
    then a second poll from newStartPageToken that returns no changes. This lets the
    incremental check confirm the durable cursor does not replay f1."""
    respx_mock.get(path="/drive/v3/changes/startPageToken").mock(
        return_value=httpx.Response(200, json={"startPageToken": "S1"})
    )

    def _changes(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("pageToken")
        if token == "S1":
            return httpx.Response(
                200,
                json={
                    "changes": [
                        {"changeType": "file", "removed": False, "fileId": "f1"},
                    ],
                    "newStartPageToken": "S2",
                },
            )
        # Any later poll (from the durable checkpoint S2) has nothing new.
        return httpx.Response(200, json={"changes": [], "newStartPageToken": "S2"})

    respx_mock.get(path="/drive/v3/changes").mock(side_effect=_changes)


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_drive_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    _seed_change_feed(respx_mock)
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["f1"]


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_drive_conforms(respx_mock: respx.MockRouter) -> None:
    """The full shared suite: seeds the change feed, fetch, list_acls, and asserts the
    incremental cursor is a real non-empty resume point that does not replay f1."""
    _seed_change_feed(respx_mock)
    respx_mock.get(path="/drive/v3/files/f1").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "id": "f1",
                    "name": "Doc",
                    "mimeType": "text/plain",
                    "webViewLink": "https://drive.example/f1",
                },
            ),
            httpx.Response(200, text="hello"),
        ]
    )
    respx_mock.get(path="/drive/v3/files/f1/permissions").mock(
        return_value=httpx.Response(
            200,
            json={
                "permissions": [
                    {"type": "user", "emailAddress": "alice@x.com"},
                ]
            },
        )
    )
    # assert_conforms also exercises list_resources for the resource_discovery flag.
    respx_mock.get(path="/drive/v3/drives").mock(
        return_value=httpx.Response(200, json={"drives": [{"id": "d1", "name": "Eng"}]})
    )
    await assert_conforms(_factory())


@pytest.mark.conformance
async def test_drive_emits_deletion_tombstone() -> None:
    """removed==true and file.trashed==true each surface as a deleted DocRef; a live
    change stays deleted=False. This pins the deletions capability the connector claims."""

    @respx.mock(assert_all_called=False)
    async def _run(respx_mock: respx.MockRouter) -> list[tuple[str, bool]]:
        respx_mock.get(path="/drive/v3/changes/startPageToken").mock(
            return_value=httpx.Response(200, json={"startPageToken": "S1"})
        )
        respx_mock.get(path="/drive/v3/changes").mock(
            return_value=httpx.Response(
                200,
                json={
                    "changes": [
                        {"changeType": "file", "removed": False, "fileId": "live"},
                        {"changeType": "file", "removed": True, "fileId": "gone"},
                        {
                            "changeType": "file",
                            "removed": False,
                            "fileId": "trashed",
                            "file": {"trashed": True},
                        },
                    ],
                    "newStartPageToken": "S2",
                },
            )
        )
        conn = _factory()
        return [(ref.source_doc_id, ref.deleted) async for ref, _ in conn.list_changes(None)]

    seen = await _run()
    assert ("live", False) in seen
    assert ("gone", True) in seen
    assert ("trashed", True) in seen


@pytest.mark.conformance
async def test_drive_initial_cursor_is_non_empty() -> None:
    """A None cursor takes a fresh startPageToken, so even the first pass yields a
    real durable cursor (never the empty string the SDK contract rejects)."""

    @respx.mock(assert_all_called=False)
    async def _run(respx_mock: respx.MockRouter) -> list[str]:
        respx_mock.get(path="/drive/v3/changes/startPageToken").mock(
            return_value=httpx.Response(200, json={"startPageToken": "S1"})
        )
        respx_mock.get(path="/drive/v3/changes").mock(
            return_value=httpx.Response(
                200,
                json={
                    "changes": [{"changeType": "file", "removed": False, "fileId": "f1"}],
                    "newStartPageToken": "S2",
                },
            )
        )
        conn = _factory()
        return [cursor async for _, cursor in conn.list_changes(None)]

    cursors = await _run()
    assert cursors == ["S2"]
    assert all(c for c in cursors)


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_drive_permissions(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/drive/v3/files/f1/permissions").mock(
        return_value=httpx.Response(
            200,
            json={
                "permissions": [
                    {"type": "group", "emailAddress": "eng@x.com"},
                    {"type": "user", "emailAddress": "alice@x.com"},
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


@pytest.mark.conformance
@pytest.mark.permission
async def test_drive_acl_paginates_and_namespaces_domain() -> None:
    """permissions.list is followed across nextPageToken so a heavily shared file is
    never truncated, and a domain grant lands in allow_groups as "domain:<domain>"."""

    @respx.mock(assert_all_called=True)
    async def _run(respx_mock: respx.MockRouter):
        def _perms(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("pageToken") == "P2":
                return httpx.Response(
                    200,
                    json={
                        "permissions": [
                            {"type": "domain", "domain": "x.com"},
                            {"type": "anyone"},
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "permissions": [
                        {"type": "user", "emailAddress": "alice@x.com"},
                        {"type": "group", "emailAddress": "eng@x.com"},
                    ],
                    "nextPageToken": "P2",
                },
            )

        respx_mock.get(path="/drive/v3/files/f9/permissions").mock(side_effect=_perms)
        from pansoph_types import DocRef

        conn = _factory()
        return await conn.list_acls(DocRef(source_doc_id="f9", instance_id="drive"))

    acl = await _run()
    assert acl.public is True
    assert set(acl.allow_users) == {"alice@x.com"}
    assert set(acl.allow_groups) == {"eng@x.com", "domain:x.com"}
    assert acl.deny == []
