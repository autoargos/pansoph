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

from pansoph_authz import Principal
from pansoph_retrieval.pipeline import retrieve

from . import assembly


def build_mcp() -> Any:
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("pansoph")

    @server.tool()
    async def pansoph_search(
        query: str, user: str, tenant_id: str = "default", groups: list[str] | None = None
    ) -> str:
        """Search the operator's permitted knowledge. Returns cited chunks the user may read."""
        hits = await retrieve(
            query=query,
            subject=Principal(type="user", id=user, ns=tenant_id),
            tenant_id=tenant_id,
            subject_groups=groups or [],
            final_k=8,
            vector_store=assembly.build_vector_store(),
            embedder=assembly.build_embedder(),
            authz=assembly.build_authz(),
            reranker=assembly.build_reranker(),
        )
        if not hits:
            return "No permitted results."
        return "\n\n".join(f"[{i + 1}] {h.doc_id}\n{h.text}" for i, h in enumerate(hits))

    return server


def main() -> None:
    build_mcp().run(transport="streamable-http")


if __name__ == "__main__":
    main()
