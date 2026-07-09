"""The FastAPI application: REST, health, and the SSE chat stream.

This is the thin surface. Route handlers will delegate to pansoph-retrieval and
pansoph-ingestion once the adapters are wired. The SSE endpoint follows the AI SDK
UI Message Stream protocol so any frontend can consume it.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI

from . import __version__
from .routes import router
from .routes_group_aliases import router as group_aliases_router
from .routes_resources import router as resources_router
from .settings import Settings


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start the in-process connector sync scheduler. Best effort: if it cannot be
    started (for example Postgres is not up yet), agora still serves; the scheduler
    retries each tick. Production runs syncs on the durable engine instead."""
    from .assembly import _postgres_dsn
    from .connectors import ConnectorManager
    from .scheduler import scheduler_loop

    task: asyncio.Task[None] | None = None
    with contextlib.suppress(Exception):
        task = asyncio.create_task(scheduler_loop(ConnectorManager(_postgres_dsn())))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="pansoph", version=__version__, lifespan=_lifespan)

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

    # Search, chat (SSE), and discovery routes. They enforce permissions through the
    # authz bulk check inside the retrieval pipeline.
    app.include_router(router)
    # Connector resource-discovery and credential-status reads (connect-then-pick UX).
    app.include_router(resources_router)
    # Identity-group aliases: connect an IdP group to a connector source group so its
    # fail-closed grants become reachable by the mapped users.
    app.include_router(group_aliases_router)
    return app
