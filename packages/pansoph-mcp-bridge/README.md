# pansoph-mcp-bridge

Two directions, one package.

1. Inbound bridge: turn any Model Context Protocol server into a connector. The MCP ecosystem passed ten thousand public servers, so the bridge turns a very large catalog into instant connectors at near zero marginal cost. The bridge maps MCP resources and tools to documents, honoring the server's own auth. It never bypasses authentication.

2. Outbound server: expose pansoph itself as an MCP server, so other agents can search and chat over an operator's knowledge through MCP, permission aware.

Granola is the first proof of the inbound bridge, since it has no bulk transcript API and is reached through its MCP server.

This package is usable on its own: point it at an MCP server and get a `Connector`.
