"""Hermetic conformance for the dropbox connector, driven by respx mocks.

No live credentials: every Dropbox HTTP API v2 call the connector makes is mocked
with a representative payload. Dropbox uses POST with JSON bodies, so each route is
registered on respx_mock.post by path. The permission category asserts the ACL maps
to the users (allow_users) and groups (allow_groups) the credential can see.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_dropbox.connector import DropboxConnector
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions

_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> DropboxConnector:
    return DropboxConnector(instance_id="dbx", config={"token": "x"})


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_dropbox_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(path="/2/files/list_folder").mock(
        return_value=httpx.Response(
            200,
            json={
                "entries": [{".tag": "file", "id": "id:F1", "name": "a.txt"}],
                "has_more": False,
            },
        )
    )
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["id:F1"]


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_dropbox_permissions(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(path="/2/sharing/list_file_members").mock(
        return_value=httpx.Response(
            200,
            json={
                "users": [{"user": {"email": "alice@x.com"}}],
                "groups": [{"group": {"group_name": "eng"}}],
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
