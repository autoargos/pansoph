"""agora as an MCP server (outbound).

Exposes a permission-aware search tool over MCP, so other agents can query an
operator's knowledge through the Model Context Protocol. The tool runs the same
retrieval enforcement path as the HTTP API, so it never returns a document the
subject may not read.

Runs as its own process (`pansoph-mcp`) over streamable HTTP, which avoids the
sub-app lifespan coupling of mounting it inside the FastAPI app. It can be mounted
into agora later once the session-manager lifespan is wired.
"""

from __future__ import annotations

from typing import Any

from . import assembly


def build_mcp() -> Any:
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("pansoph")

    @server.tool()
    async def pansoph_search(query: str, token: str) -> str:
        """Search the operator's permitted knowledge. Returns cited chunks the caller may
        read.

        SECURITY: `token` is the caller's bearer token (the same one the HTTP API takes).
        Identity, tenant, and groups are derived from the VALIDATED token via the Identity
        port, never from tool arguments, so an MCP client cannot impersonate a user or read
        another tenant. An invalid or missing token returns nothing.
        """
        # Imported here to avoid a module-level dependency cycle with routes.
        from .routes import _retrieve

        user = await assembly.build_identity().authenticate(token)
        if user is None:
            return "Authentication required: provide a valid bearer token."
        subject = getattr(user, "email", None) or user.id
        tenant = getattr(user, "tenant_id", "") or "default"
        groups = list(getattr(user, "group_ids", []))
        # Runs the exact HTTP enforcement path: group-alias expansion, membership sync, the
        # overfetch plus bulk check, and the deny filter, so it never returns a document the
        # subject may not read.
        hits = await _retrieve(subject, groups, tenant, query, 8)
        if not hits:
            return "No permitted results."
        return "\n\n".join(f"[{i + 1}] {h.doc_id}\n{h.text}" for i, h in enumerate(hits))

    return server


def main() -> None:
    build_mcp().run(transport="streamable-http")


if __name__ == "__main__":
    main()
