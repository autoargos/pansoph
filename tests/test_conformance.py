"""Run the shared conformance suite against the reference connector.

This keeps the harness itself honest. The exhaustive per-connector conformance
runs are part of task #21.
"""

from __future__ import annotations

from pansoph_connector_sdk import InMemoryConnector, assert_conforms
from pansoph_types import AccessControl


async def test_reference_connector_conforms() -> None:
    c = InMemoryConnector()
    c.put("doc1", "hello world", AccessControl(allow_groups=["g1"]))
    c.put("doc2", "second doc", AccessControl(public=True))
    await assert_conforms(c)


async def test_incremental_cursor_excludes_already_synced() -> None:
    c = InMemoryConnector()
    c.put("doc1", "one")
    # Drain the first pass, capture the last cursor.
    last = None
    async for _ref, cursor in c.list_changes(None):
        last = cursor
    # A new document appears after that cursor; only it should come back.
    c.put("doc2", "two")
    fresh = [ref.source_doc_id async for ref, _ in c.list_changes(last)]
    assert fresh == ["doc2"]
