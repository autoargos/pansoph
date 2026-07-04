"""An in-memory reference connector.

It fully implements the contract over a dict of documents, so the conformance
suite has something real to verify and demos and tests have a connector that needs
no network or credentials. Cursors are integer revisions encoded as strings, which
exercises incremental sync honestly: a second list with the last cursor yields
nothing new.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc

from .contract import ChangeEvent, ConnectorMeta, ValidationResult
from .spec import ConnectorSpec


class _Entry:
    __slots__ = ("raw", "acl", "rev", "deleted")

    def __init__(self, raw: RawDoc, acl: AccessControl, rev: int, deleted: bool = False) -> None:
        self.raw = raw
        self.acl = acl
        self.rev = rev
        self.deleted = deleted


class InMemoryConnector:
    def __init__(self, instance_id: str = "mem", config: dict[str, Any] | None = None) -> None:
        # Accept (and ignore) config so the reference connector is instantiated the
        # same way the runtime builds every connector: cls(instance_id, config).
        self.instance_id = instance_id
        self._docs: dict[str, _Entry] = {}
        self._rev = 0
        self.meta = ConnectorMeta(
            name="memory",
            auth=AuthScheme.none,
            capabilities=Capabilities(
                incremental=True, permission_sync=True, deletions=True, slim_listing=True
            ),
        )

    @classmethod
    def spec(cls) -> ConnectorSpec:
        # The in-memory reference connector reads no config and needs no credentials:
        # __init__ accepts and ignores config, validate always returns ok, and auth is
        # AuthScheme.none. Its spec is therefore honest with zero fields (and zero
        # secrets). Do not add fields the connector does not actually read.
        return ConnectorSpec(
            name="memory",
            title="In-memory (reference)",
            description=(
                "A built-in reference connector backed by an in-memory dict. Needs no "
                "network and no credentials. Used for demos, tests, and conformance."
            ),
            fields=(),
        )

    # --- test/demo helpers ---
    def put(self, source_doc_id: str, text: str, acl: AccessControl | None = None) -> None:
        self._rev += 1
        raw = RawDoc(
            source_doc_id=source_doc_id,
            instance_id=self.instance_id,
            mime="text/plain",
            text=text,
        )
        self._docs[source_doc_id] = _Entry(raw, acl or AccessControl(public=True), self._rev)

    def remove(self, source_doc_id: str) -> None:
        """Tombstone a document so list_changes surfaces it as deleted=True.

        The entry is kept (not popped) and bumped to a new revision with
        deleted=True, so an incremental list from a cursor before the removal
        yields the tombstone, exactly as a real connector reports a deletion.
        """
        entry = self._docs.get(source_doc_id)
        if entry is None:
            return
        self._rev += 1
        entry.rev = self._rev
        entry.deleted = True

    # --- contract ---
    def validate(self, config: dict[str, Any]) -> ValidationResult:
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        since = int(cursor) if cursor else 0
        for entry in sorted(self._docs.values(), key=lambda e: e.rev):
            if entry.rev > since:
                ref = DocRef(
                    source_doc_id=entry.raw.source_doc_id,
                    instance_id=self.instance_id,
                    deleted=entry.deleted,
                )
                yield ref, str(entry.rev)

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        entry = self._docs[ref.source_doc_id]
        return entry.raw, entry.acl

    async def list_acls(self, ref: DocRef) -> AccessControl:
        return self._docs[ref.source_doc_id].acl

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        return []
