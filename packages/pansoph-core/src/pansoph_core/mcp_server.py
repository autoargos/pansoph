"""agora as an MCP server (outbound).

Exposes a permission-aware search tool over MCP, so other agents can query an
operator's knowledge through the Model Context Protocol. The tool runs the same
retrieval enforcement path as the HTTP API, so it never returns a document the
subject may not read.

Runs as its own process (`pansoph-mcp`) over streamable HTTP, which avoids the
sub-app lifespan coupling of mounting it inside the FastAPI app. It can be mounted
into agora later once the session-manager lifespan is wired.

Authentication is at the transport (see mcp_auth.py): the caller's bearer token rides
the HTTP Authorization header and is validated before any tool runs. No tool takes a
credential argument.
"""

from __future__ import annotations

from typing import Any

from .mcp_auth import IdentityTokenVerifier, auth_settings_from_env, current_principal


def build_mcp(auth: Any = None, token_verifier: Any = None) -> Any:
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(
        "pansoph",
        token_verifier=token_verifier or IdentityTokenVerifier(),
        auth=auth or auth_settings_from_env(),
    )

    @server.tool()
    async def pansoph_search(query: str) -> str:
        """Search the operator's permitted knowledge. Returns cited chunks the caller may
        read.

        SECURITY: identity, tenant, and groups come from the caller's bearer token, which
        the MCP transport validated before this tool ran, never from tool arguments, so an
        MCP client cannot impersonate a user or read another tenant.
        """
        # Imported here to avoid a module-level dependency cycle with routes.
        from .audit import audit
        from .routes import _retrieve

        who = current_principal()
        # Runs the exact HTTP enforcement path: group-alias expansion, membership sync, the
        # overfetch plus bulk check, and the deny filter, so it never returns a document the
        # subject may not read.
        hits = await _retrieve(who.principal, who.groups, who.tenant_id, query, 8)
        audit(
            "mcp_search",
            actor=who.principal,
            tenant=who.tenant_id,
            results=len(hits),
            doc_ids=sorted({h.doc_id for h in hits}),
        )
        if not hits:
            return "No permitted results."
        return "\n\n".join(f"[{i + 1}] {h.doc_id}\n{h.text}" for i, h in enumerate(hits))

    return server


def main() -> None:
    build_mcp().run(transport="streamable-http")


if __name__ == "__main__":
    main()
