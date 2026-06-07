"""The FastAPI application: REST, health, and a placeholder SSE chat stream.

This is the thin surface. Route handlers will delegate to pansoph-retrieval and
pansoph-ingestion once the adapters are wired. The SSE endpoint follows the AI SDK
UI Message Stream protocol so any frontend can consume it.
"""

from __future__ import annotations

from fastapi import FastAPI

from . import __version__
from .settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="pansoph", version=__version__)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/meta")
    async def meta() -> dict[str, object]:
        return {
            "version": __version__,
            "env": settings.env,
            "cloud_model": settings.is_cloud_model(),
            "adapters": {
                "vector": settings.vector_adapter,
                "authz": settings.authz_adapter,
                "identity": settings.identity_adapter,
                "llm": settings.llm_adapter,
                "embedder": settings.embedder_adapter,
                "reranker": settings.reranker_adapter,
                "parser": settings.parser_adapter,
            },
        }

    # POST /api/chat (SSE), /api/search, /api/connectors, /api/access land with the
    # core assembly phase. See ADR-0002.
    return app
