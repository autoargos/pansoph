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

from pansoph_connector_sdk import ConnectorMeta
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
