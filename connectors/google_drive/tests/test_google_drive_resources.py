"""Hermetic discovery tests for the google_drive connector's list_resources.

No live OAuth token: the Drive v3 drives.list and files.list calls are mocked with
representative payloads. These assert the connect-then-pick discovery: the top level
lists shared drives, drilling into a drive lists its child folders, and the source's
pageToken is surfaced as the opaque cursor and threaded back to fetch the next page.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from pansoph_connector_google_drive.connector import GoogleDriveConnector
from pansoph_connector_sdk import Resource, ResourcePage


def _factory() -> GoogleDriveConnector:
    return GoogleDriveConnector(instance_id="drive", config={"token": "x"})


def test_connector_declares_resource_discovery() -> None:
    conn = _factory()
    assert conn.meta.capabilities.resource_discovery is True


def test_spec_binds_resource_kinds() -> None:
    spec = GoogleDriveConnector.spec()
    assert spec.get("drive_id").resource_kind == "drive"
    assert spec.get("folder_id").resource_kind == "folder"


@pytest.mark.conformance
@respx.mock(assert_all_called=True)
async def test_list_drives_top_level(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(path="/drive/v3/drives").mock(
        return_value=httpx.Response(
            200,
            json={
                "drives": [
                    {"id": "d1", "name": "Engineering"},
                    {"id": "d2", "name": "Sales"},
                ]
            },
        )
    )
    conn = _factory()
    page = await conn.list_resources()

    assert isinstance(page, ResourcePage)
    assert page.items == [
        Resource(id="d1", kind="drive", label="Engineering"),
        Resource(id="d2", kind="drive", label="Sales"),
    ]
    assert page.next_cursor is None  # no nextPageToken means the listing is exhausted
    # Top level hits drives.list, not files.list.
    assert route.called


@pytest.mark.conformance
@respx.mock(assert_all_called=True)
async def test_list_folders_under_parent(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(path="/drive/v3/files").mock(
        return_value=httpx.Response(
            200,
            json={
                "files": [
                    {"id": "f1", "name": "Specs"},
                    {"id": "f2", "name": "Archive"},
                ]
            },
        )
    )
    conn = _factory()
    page = await conn.list_resources(kind="folder", parent_id="d1")

    assert page.items == [
        Resource(id="f1", kind="folder", label="Specs", parent_id="d1"),
        Resource(id="f2", kind="folder", label="Archive", parent_id="d1"),
    ]
    assert page.next_cursor is None
    # The folder query confines the read to children of the parent, folders only.
    q = route.calls.last.request.url.params["q"]
    assert "mimeType = 'application/vnd.google-apps.folder'" in q
    assert "'d1' in parents" in q
    # Shared-drive folders stay reachable.
    assert route.calls.last.request.url.params["supportsAllDrives"] == "true"


@pytest.mark.conformance
@respx.mock(assert_all_called=True)
async def test_list_drives_paginates(respx_mock: respx.MockRouter) -> None:
    # First page returns a nextPageToken; second page (called back with that cursor)
    # returns none, so the caller knows the listing is exhausted.
    responses = [
        httpx.Response(
            200, json={"drives": [{"id": "d1", "name": "Engineering"}], "nextPageToken": "TKN2"}
        ),
        httpx.Response(200, json={"drives": [{"id": "d2", "name": "Sales"}]}),
    ]
    route = respx_mock.get(path="/drive/v3/drives").mock(side_effect=responses)
    conn = _factory()

    first = await conn.list_resources()
    assert [r.id for r in first.items] == ["d1"]
    assert first.next_cursor == "TKN2"

    second = await conn.list_resources(cursor=first.next_cursor)
    assert [r.id for r in second.items] == ["d2"]
    assert second.next_cursor is None

    assert route.call_count == 2
    # The opaque cursor is passed back verbatim as the source's pageToken.
    assert route.calls[1].request.url.params["pageToken"] == "TKN2"


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_folders_without_parent_is_empty(respx_mock: respx.MockRouter) -> None:
    # A folder kind with no parent has no tree to walk (the top level is drives),
    # so it returns an empty page without hitting the source.
    files = respx_mock.get(path="/drive/v3/files")
    conn = _factory()
    page = await conn.list_resources(kind="folder")

    assert page.items == []
    assert page.next_cursor is None
    assert not files.called
