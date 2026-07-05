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

from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc, ResourcePage
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
    carries that instance's schedule and ACL namespace. The runtime persists the
    cursor returned by `list_changes` and replays it on the next durable sync.

    Auth (optional convention): a connector that needs a refreshing credential may
    expose `auth_requirement(self) -> AuthRequirement | None` (from
    `pansoph_connector_sdk.auth_types`). When it returns a requirement, the runtime
    builds a TokenSource from the configured secret store and the process-wide
    credential provider and sets it as `self.token_source`; the connector then calls
    `self.token_source.get_access_token()` per request and never holds a raw secret.
    A connector that does not expose the method keeps using its own config-based
    auth, so the convention is fully backward compatible.
    """

    meta: ConnectorMeta

    def validate(self, config: dict[str, Any]) -> ValidationResult:
        """Cheap check that the config and credentials are usable."""
        ...

    def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        """Yield (DocRef, next_cursor) for everything changed since `cursor`.

        The runtime checkpoints `next_cursor` as a durable step output, so a crash
        resumes from the last yielded cursor rather than from zero. `cursor` is the
        durable resume point the source defines (a high-water mark, a delta link,
        a page token), never an intra-listing page token reused across runs.

        To report a removal, yield a DocRef with `deleted=True` (a tombstone): the
        runtime purges that document and its access tuples. A connector that
        declares the deletions capability but never yields a deleted DocRef is not
        actually implementing deletions, which the conformance suite checks.
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

    async def list_resources(
        self,
        kind: str | None = None,
        parent_id: str | None = None,
        cursor: str | None = None,
    ) -> ResourcePage:
        """Optional. Enumerate what the configured credential can reach, for a picker.

        Present only when the connector declares the `resource_discovery` capability,
        which the conformance suite pairs exactly like deletions: declaring the flag
        requires implementing this method. It lists resources at the source's own
        granularity, filtered to what the credential already permits (so discovery is
        a read, never a scope escalation), hierarchical by `(kind, parent_id)` so the
        UI drills org to repo, drive to folder, workspace to channel, and paginated by
        the opaque `cursor` returned in the previous `ResourcePage`. A None `kind`
        means the top level; a None `cursor` means the first page. It reuses the same
        TokenSource as sync, so no separate credential is minted for discovery.
        """
        ...
