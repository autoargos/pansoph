"""Hermetic conformance for the google_drive connector, driven by respx mocks.

No live OAuth token: the Drive v3 files.list and permissions.list calls are mocked
with representative payloads. The permission category asserts the file permissions
map to allow_users (user emails) and allow_groups (group and domain).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_google_drive.connector import GoogleDriveConnector
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions

_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> GoogleDriveConnector:
    return GoogleDriveConnector(instance_id="drive", config={"token": "x"})


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_drive_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/drive/v3/files").mock(
        return_value=httpx.Response(
            200, json={"files": [{"id": "f1", "mimeType": "text/plain", "modifiedTime": "t"}]}
        )
    )
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["f1"]


@pytest.mark.conformance
@pytest.mark.permission
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
