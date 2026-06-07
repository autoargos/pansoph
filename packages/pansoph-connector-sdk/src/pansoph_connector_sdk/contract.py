"""The connector contract.

A connector is anything that satisfies the `Connector` protocol. Implement it
directly in Python for awkward sources, or describe a simple REST source with a
declarative manifest (see `manifest.py`) which the runtime turns into a
`Connector` for you. Either way, the runtime owns pagination, retries, backoff,
cursors, and resumable sync, so a connector only describes how to talk to its
source.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc
from pydantic import BaseModel


class ValidationResult(BaseModel):
    ok: bool
    error: str | None = None


class ChangeEvent(BaseModel):
    """A normalized change emitted by a webhook, resolved to DocRefs by the runtime."""

    kind: str  # "upsert" | "delete" | "acl_change"
    source_doc_id: str
    instance_id: str
    raw: dict[str, Any] = {}


class ConnectorMeta(BaseModel):
    """Static description of a connector type, surfaced in admin and used by the runtime."""

    name: str  # e.g. "github"
    auth: AuthScheme
    capabilities: Capabilities
    version: str = "0.0.1"


@runtime_checkable
class Connector(Protocol):
    """The contract every connector satisfies.

    Implementations are instantiated per configured instance, so `self` already
    carries that instance's credentials, schedule, and ACL namespace. The runtime
    persists the cursor returned by `list_changes` and replays it on the next
    durable sync.
    """

    meta: ConnectorMeta

    def validate(self, config: dict[str, Any]) -> ValidationResult:
        """Cheap check that the config and credentials are usable."""
        ...

    def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        """Yield (DocRef, next_cursor) for everything changed since `cursor`.

        The runtime checkpoints `next_cursor` as a durable step output, so a crash
        resumes from the last yielded cursor rather than from zero.
        """
        ...

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        """Fetch one document and its source access control list together."""
        ...

    async def list_acls(self, ref: DocRef) -> AccessControl:
        """Return just the ACL for a document. Used when permission_sync is set."""
        ...

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        """Optional. Normalize a source webhook into change events for freshness."""
        ...
