"""Incremental and deletions categories.

incremental: a full pass collects refs and the last cursor; replaying with that
cursor must yield no already-seen doc, and an abnormally large state must yield
zero records. deletions: removing a source doc surfaces as a delete change, which
the caller verifies through the connector's own removal helper or webhook path.
"""

from __future__ import annotations

from ...contract import Connector


async def check_incremental(connector: Connector, abnormal_state: str | None) -> None:
    refs = []
    last_cursor: str | None = None
    async for ref, cursor in connector.list_changes(None):
        assert cursor, "an incremental connector must yield a non-empty cursor"
        refs.append(ref.source_doc_id)
        last_cursor = cursor
    assert refs, "seed the connector before the incremental check"

    seen = set(refs)
    if last_cursor is not None:
        async for ref, _ in connector.list_changes(last_cursor):
            assert ref.source_doc_id not in seen, (
                "incremental cursor leaked an already-synced document"
            )

    if abnormal_state is not None:
        async for _ref, _cursor in connector.list_changes(abnormal_state):
            raise AssertionError("abnormally large state must yield zero records")
