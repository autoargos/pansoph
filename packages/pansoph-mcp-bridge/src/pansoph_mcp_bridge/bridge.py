"""Inbound bridge: map an MCP server's resources to documents.

Implements the connector contract on top of an MCP client session over streamable
HTTP. Listing maps to resources/list, fetching to resources/read. MCP resources do
not carry access control, so the instance configuration sets the ACL (public, or a
set of groups), namespaced by the instance. This is the answer for the many sources
that ship an MCP server but no bulk API, Granola being the first proof.

The mcp SDK is a dependency of this package.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pansoph_connector_sdk import ChangeEvent, ConnectorMeta, ValidationResult
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc


class MCPConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.instance_id = instance_id
        self._server_url: str = cfg.get("server_url", "")
        self._public: bool = bool(cfg.get("public", False))
        self._allow_groups: list[str] = list(cfg.get("allow_groups", []))
        # Confine the instance to resources whose URI starts with this prefix, so one
        # MCP server can back several scoped instances. Empty means all resources.
        self._uri_prefix: str = cfg.get("resource_uri_prefix", "")
        self.meta = ConnectorMeta(
            name="mcp",
            auth=AuthScheme.oauth2,
            capabilities=Capabilities(slim_listing=True),
        )

    def _acl(self) -> AccessControl:
        return AccessControl(
            public=self._public, allow_groups=self._allow_groups, principal_ns=self.instance_id
        )

    def validate(self, config: dict[str, Any]) -> ValidationResult:
        if not config.get("server_url"):
            return ValidationResult(ok=False, error="missing server_url")
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(self._server_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_resources()
                for res in result.resources:
                    uri = str(res.uri)
                    if self._uri_prefix and not uri.startswith(self._uri_prefix):
                        continue
                    yield DocRef(source_doc_id=uri, instance_id=self.instance_id), ""

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(self._server_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.read_resource(ref.source_doc_id)
                text = "\n".join(
                    getattr(c, "text", "") for c in result.contents if getattr(c, "text", None)
                )
                raw = RawDoc(
                    source_doc_id=ref.source_doc_id,
                    instance_id=self.instance_id,
                    mime="text/plain",
                    text=text,
                )
                return raw, self._acl()

    async def list_acls(self, ref: DocRef) -> AccessControl:
        return self._acl()

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        return []
