"""The deletion contract: a deletions-capable connector surfaces removals as
DocRefs with deleted=True, and check_deletions catches a connector that declares
the capability but never emits one. Fully offline. No em dashes anywhere.
"""

from __future__ import annotations

import pytest
from pansoph_connector_sdk import InMemoryConnector
from pansoph_connector_sdk.verify.categories.sync import check_deletions
from pansoph_types import AccessControl, DocRef


async def _collect(connector: InMemoryConnector, cursor: str | None) -> list[DocRef]:
    return [ref async for ref, _ in connector.list_changes(cursor)]


async def test_remove_surfaces_a_deleted_docref() -> None:
    c = InMemoryConnector(instance_id="mem")
    c.put("a", "doc a", AccessControl(public=True))  # rev 1
    c.put("b", "doc b", AccessControl(public=True))  # rev 2

    # A full sync up to rev 2 has no tombstones.
    full = await _collect(c, None)
    assert {ref.source_doc_id for ref in full} == {"a", "b"}
    assert all(not ref.deleted for ref in full)

    c.remove("b")  # rev 3 tombstone

    # Listing from the pre-removal cursor surfaces b as a deletion, and nothing else.
    after = await _collect(c, "2")
    assert len(after) == 1
    assert after[0].source_doc_id == "b"
    assert after[0].deleted is True


async def test_check_deletions_passes_when_a_tombstone_surfaces() -> None:
    c = InMemoryConnector(instance_id="mem")
    c.put("a", "doc a", AccessControl(public=True))
    c.put("b", "doc b", AccessControl(public=True))
    c.remove("b")  # tombstone at the latest revision
    # Listing from the start surfaces the tombstone, so the check passes.
    await check_deletions(c, deleted_state=None)


async def test_check_deletions_fails_when_capability_is_unimplemented() -> None:
    # A connector that only ever upserts (never tombstones) declares the deletions
    # capability but does not implement it. check_deletions must catch that, which
    # is the exact "advertised but unimplemented" defect the audit flagged.
    c = InMemoryConnector(instance_id="mem")
    c.put("a", "doc a", AccessControl(public=True))
    c.put("b", "doc b", AccessControl(public=True))
    with pytest.raises(AssertionError, match="surfaced no deleted"):
        await check_deletions(c, deleted_state=None)
