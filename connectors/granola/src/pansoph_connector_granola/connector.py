"""The granola connector.

Granola has historically had no bulk transcript API; it is reached through its MCP
server. So this connector is the pansoph MCP bridge configured for Granola: point it
at the Granola MCP endpoint and it ingests the resources that server exposes.

  Reached via:  the Model Context Protocol (Granola's MCP server).
  Config:       "server_url" (the Granola MCP endpoint), plus the ACL fields the MCP
                bridge accepts ("public", "allow_groups"). The Granola MCP URL/token
                is the operator's to provide.
  Verified:     uses the same verified MCP client path as the relay bridge.

This is also the first proof of the bridge as a first-class tier: a source with no
bulk API still becomes a connector at near-zero marginal cost.
"""

from __future__ import annotations

from typing import Any

from pansoph_connector_sdk import ConfigField, ConnectorMeta, ConnectorSpec, FieldKind
from pansoph_mcp_bridge.bridge import MCPConnector
from pansoph_types import AuthScheme, Capabilities


class GranolaConnector(MCPConnector):
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        super().__init__(instance_id, config)
        # Same MCP machinery, branded as the Granola connector.
        self.meta = ConnectorMeta(
            name="granola",
            auth=AuthScheme.oauth2,
            capabilities=Capabilities(slim_listing=True),
        )

    @classmethod
    def spec(cls) -> ConnectorSpec:
        # The config keys are exactly those the inherited MCPConnector reads in
        # __init__ and validate(): server_url (required), plus the ACL and scoping
        # fields (public, allow_groups, resource_uri_prefix). None is a credential,
        # so none is a secret kind. See pansoph_mcp_bridge.bridge.MCPConnector.
        return ConnectorSpec(
            name="granola",
            title="Granola",
            description=(
                "Ingest Granola meeting notes and transcripts through Granola's MCP "
                "server. Point it at the Granola MCP endpoint; the resources that "
                "server exposes become documents."
            ),
            fields=(
                ConfigField(
                    key="server_url",
                    label="Granola MCP endpoint",
                    kind=FieldKind.STRING,
                    placeholder="https://mcp.granola.example/mcp",
                    help="The streamable HTTP URL of the Granola MCP server to bridge.",
                ),
                ConfigField(
                    key="public",
                    label="Public to the whole tenant",
                    kind=FieldKind.BOOL,
                    required=False,
                    help="Expose ingested Granola resources to everyone in the tenant.",
                ),
                ConfigField(
                    key="allow_groups",
                    label="Allowed groups",
                    kind=FieldKind.LIST,
                    required=False,
                    help="Groups allowed to read the ingested Granola resources.",
                ),
                ConfigField(
                    key="resource_uri_prefix",
                    label="Resource URI prefix",
                    kind=FieldKind.STRING,
                    required=False,
                    help=(
                        "Confine this instance to resources whose URI starts with this "
                        "prefix, so one MCP server can back several scoped instances. "
                        "Leave empty to ingest all resources."
                    ),
                ),
            ),
        )
