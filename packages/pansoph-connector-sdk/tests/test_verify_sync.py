"""Tests for the incremental and deletions categories."""

from __future__ import annotations

from pansoph_connector_sdk import InMemoryConnector
from pansoph_connector_sdk.verify.categories.sync import check_incremental
from pansoph_types import AccessControl


async def test_incremental_cursor_excludes_seen() -> None:
    c = InMemoryConnector(instance_id="mem")
    c.put("a", "1", AccessControl(public=True))
    c.put("b", "2", AccessControl(public=True))
    await check_incremental(c, abnormal_state="999999")  # must not raise


async def test_incremental_abnormal_state_yields_nothing() -> None:
    c = InMemoryConnector(instance_id="mem")
    c.put("a", "1", AccessControl(public=True))
    seen = [ref async for ref, _ in c.list_changes("999999")]
    assert seen == []
