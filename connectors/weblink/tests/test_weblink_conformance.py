"""Hermetic conformance for the weblink connector (public-only, no network needed)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions
from pansoph_connector_weblink.connector import WeblinkConnector

_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> WeblinkConnector:
    return WeblinkConnector(instance_id="web", config={"urls": ["https://example.test/a"]})


@pytest.mark.conformance
async def test_weblink_discovery() -> None:
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert refs[0].source_doc_id == "https://example.test/a"


@pytest.mark.conformance
@pytest.mark.permission
async def test_weblink_permissions_public_only() -> None:
    conn = _factory()
    await check_permissions(
        conn,
        source_acl=_FIX / "principals.json",
        principals=_FIX / "principals.json",
        expected=_FIX / "expected.json",
    )
