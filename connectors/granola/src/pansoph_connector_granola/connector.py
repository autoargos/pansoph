"""The granola connector.

IMPLEMENTATION RULE (see pansoph CLAUDE.md section 6): implement against the latest
official granola API documentation. Do not implement from memory. Before filling in
the methods below, verify and then record here:

  API docs:        <official docs URL>
  API version:     <version or date>
  Auth and scopes: <method and required scopes>
  Pagination:      <how list pages work>
  Incremental:     <cursor, change feed, or webhook mechanism>
  Rate limits:     <limits and how this connector respects them>
  ACL model:       <how source permissions map to AccessControl>
  Verified on:     <YYYY-MM-DD by whom>

Getting the ACL mapping wrong is a security bug. The runtime owns pagination,
retries, backoff, and resumable sync, so a method only describes how to talk to the
source for one page or one document.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pansoph_connector_sdk import ChangeEvent, ConnectorMeta, ValidationResult
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc


class GranolaConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        self.instance_id = instance_id
        self._config = config or {}
        self.meta = ConnectorMeta(
            name="granola",
            auth=AuthScheme.oauth2,
            capabilities=Capabilities(
                incremental=True, permission_sync=True, deletions=True, slim_listing=True
            ),
        )

    def validate(self, config: dict[str, Any]) -> ValidationResult:
        """Cheaply verify the config and credentials, ideally with one light call."""
        raise NotImplementedError("validate: implement against the latest granola API docs")

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        """Yield (DocRef, next_cursor) for everything changed since `cursor`.

        Persist nothing yourself; the runtime checkpoints the cursor you yield.
        """
        raise NotImplementedError("list_changes: implement against the latest granola API docs")
        yield  # pragma: no cover  (marks this as an async generator)

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        """Fetch one document and its source ACL together."""
        raise NotImplementedError("fetch: implement against the latest granola API docs")

    async def list_acls(self, ref: DocRef) -> AccessControl:
        """Return just the ACL for a document. Used when permission_sync is set."""
        raise NotImplementedError("list_acls: implement against the latest granola API docs")

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        """Optional. Normalize a source webhook into change events for freshness."""
        return []
