"""Hermetic conformance for the SharePoint connector, driven by respx mocks.

No live credentials: every Microsoft Graph call the connector makes is mocked with a
representative payload. The permission category asserts a driveItem's ACL maps its
grantedToV2 user grants to allow_users and group grants to allow_groups, namespaced by
the instance. respx matches on path only (query strings are ignored), so the @odata
pagination links and Graph query parameters do not affect the matches.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_connector_sharepoint.connector import SharepointConnector

_FIX = Path(__file__).parent / "fixtures" / "permissions"
_CONFIG = {"token": "x", "drive_id": "D1", "base_url": "https://graph.microsoft.com"}


def _factory() -> SharepointConnector:
    return SharepointConnector(instance_id="contoso", config=_CONFIG)


@pytest.mark.conformance
@respx.mock(assert_all_called=False)
async def test_sharepoint_discovery_and_spec(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/v1.0/drives/D1/root/children").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "I1",
                        "name": "a.docx",
                        "file": {
                            "mimeType": (
                                "application/vnd.openxmlformats-officedocument."
                                "wordprocessingml.document"
                            )
                        },
                    }
                ]
            },
        )
    )
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["I1"]


@pytest.mark.conformance
@pytest.mark.permission
@respx.mock(assert_all_called=False)
async def test_sharepoint_permissions(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(path="/v1.0/drives/D1/items/I1/permissions").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {"grantedToV2": {"user": {"id": "alice@x.com"}}},
                    {"grantedToV2": {"group": {"id": "eng"}}},
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
