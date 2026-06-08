"""A tiny sample MCP server that exposes a few resources over streamable HTTP.

It exists so the MCP bridge connector can be demonstrated end to end without an
external MCP server. The pansoph MCP bridge calls resources/list and resources/read,
which this server answers. Run inside the stack; the bridge points at
http://mcp-sample:8000/mcp.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("pansoph-sample", host="0.0.0.0", port=8000)

_NOTES = {
    "standup": (
        "Standup notes, 2026-06-08. Shipped the hosted auth provider and OIDC login. "
        "RBAC now enforced from a real login. Next: wire more connectors."
    ),
    "retro": (
        "Sprint retro. What went well: the connector SDK and the permission red team. "
        "What to improve: connector OAuth onboarding."
    ),
    "glossary": (
        "Glossary. agora is the backend, lens the UI, scribe the workers, scout the "
        "browser grabber, relay the MCP bridge."
    ),
}


@mcp.resource("notes://standup")
def standup() -> str:
    return _NOTES["standup"]


@mcp.resource("notes://retro")
def retro() -> str:
    return _NOTES["retro"]


@mcp.resource("notes://glossary")
def glossary() -> str:
    return _NOTES["glossary"]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
