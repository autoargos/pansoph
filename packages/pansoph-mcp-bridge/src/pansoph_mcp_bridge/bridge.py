"""Inbound bridge: map an MCP server's resources and tools to documents.

Implements the connector contract on top of an MCP client session. Listing maps to
resources/list, fetching to resources/read, and the cursor tracks resource
revisions where the server exposes them. Stub; the client wiring lands with the
runtime.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pansoph_connector_sdk import ConnectorMeta, ValidationResult
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc


class MCPConnector:
    """A connector backed by an arbitrary MCP server."""

    def __init__(self, server_url: str, instance_id: str) -> None:
        self._server_url = server_url
        self._instance_id = instance_id
        self.meta = ConnectorMeta(
            name="mcp",
            auth=AuthScheme.oauth2,
            capabilities=Capabilities(incremental=True, slim_listing=True),
        )

    def validate(self, config: dict[str, Any]) -> ValidationResult:
        raise NotImplementedError("MCP validate lands with the runtime, see ADR-0002")

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        raise NotImplementedError("MCP list lands with the runtime")
        yield  # pragma: no cover  (marks this as an async generator)

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        raise NotImplementedError("MCP fetch lands with the runtime")

    async def list_acls(self, ref: DocRef) -> AccessControl:
        raise NotImplementedError("MCP list_acls lands with the runtime")

    async def on_webhook(self, event: dict[str, Any]) -> list[Any]:
        return []
