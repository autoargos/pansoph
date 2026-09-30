"""The agora app for the Company Memory deployment.

Upstream `create_app()` unchanged, with three things removed or added for this
deployment, so no upstream file needs a flag for them:

- `/api/grab` is not mounted: it trusts tenant, instance, and ACL from the request body
  (CM-H4), and Company Memory has no browser grabber.
- The interactive API docs and the OpenAPI schema are not mounted.
- Unsafe configuration refuses to start, and audit events go to stdout.

Run with: `uvicorn company_memory_mcp.agora_app:create_app --factory`.
"""

from __future__ import annotations

from fastapi import FastAPI
from pansoph_core.app import create_app as create_upstream_app

from .audit_logging import configure_audit_logging
from .guards import require_safe_config

REMOVED_PATHS = frozenset(
    {"/api/grab", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}
)


def create_app(check_config: bool = True) -> FastAPI:
    if check_config:
        require_safe_config()
    configure_audit_logging()
    app = create_upstream_app()
    app.router.routes = [
        r for r in app.router.routes if getattr(r, "path", None) not in REMOVED_PATHS
    ]
    app.openapi_url = None
    app.docs_url = None
    app.redoc_url = None
    return app
