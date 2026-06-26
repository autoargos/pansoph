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
            # A deletion of a previously-synced document is a legitimate change,
            # not a leak: the connector is telling us to remove it.
            if ref.deleted:
                continue
            assert ref.source_doc_id not in seen, (
                "incremental cursor leaked an already-synced document"
            )

    if abnormal_state is not None:
        async for _ref, _cursor in connector.list_changes(abnormal_state):
            raise AssertionError("abnormally large state must yield zero records")


async def check_deletions(connector: Connector, deleted_state: str | None) -> None:
    """A deletions-capable connector must surface a removal as a deleted DocRef.

    Listing from `deleted_state` (a cursor recorded just before a source document
    was removed) must yield at least one DocRef with deleted=True that still
    identifies the document. This is the check that catches the common
    "declares deletions=True but never emits a delete" defect: a connector whose
    list_changes can never produce a tombstone fails here. When `deleted_state`
    is None the check lists from the start, which suits a connector already
    seeded with a tombstone.
    """
    saw_delete = False
    async for ref, cursor in connector.list_changes(deleted_state):
        assert cursor, "list_changes must yield a non-empty cursor"
        if ref.deleted:
            saw_delete = True
            assert ref.source_doc_id, "a deleted DocRef must still identify the document"
    assert saw_delete, (
        "connector declares deletions=True but list_changes surfaced no deleted "
        "DocRef from the deletions state; implement deletion detection or stop "
        "declaring the capability"
    )
