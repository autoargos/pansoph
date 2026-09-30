"""Transport level authentication for pansoph MCP servers (CM-H6).

An MCP tool must never take a bearer token as an argument: tool arguments are written
by the model, so a token there passes through the model context, transcripts, and client
logs, and invites one shared token pasted into agent config. Instead the MCP HTTP
transport authenticates each request (`Authorization: Bearer ...`, the MCP authorization
spec's resource server role), the token is validated through the Identity port, and the
verified principal is placed in the request context. Tools read it with
`current_principal()`, so identity comes only from the trusted transport.

A missing or invalid token is rejected with 401 by the MCP SDK before any tool runs.
No em dashes anywhere.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

from .audit import audit


class PrincipalAccessToken(AccessToken):
    """The verified caller, as the MCP auth context carries it. It deliberately keeps no
    copy of the raw bearer token (the `token` field stays empty)."""

    principal: str
    groups: list[str]
    tenant_id: str


class IdentityTokenVerifier:
    """An MCP `TokenVerifier` backed by the pansoph Identity port (OIDC in practice)."""

    def __init__(self, identity_factory: Callable[[], Any] | None = None) -> None:
        if identity_factory is None:
            from . import assembly

            identity_factory = assembly.build_identity
        self._identity_factory = identity_factory
        self._identity: Any = None

    async def verify_token(self, token: str) -> AccessToken | None:
        if self._identity is None:
            self._identity = self._identity_factory()
        try:
            user = await self._identity.authenticate(token)
        except Exception:  # noqa: BLE001  any validation failure denies, never allows
            user = None
        if user is None:
            audit("mcp_auth", actor="anonymous", tenant="", decision="denied", reason="invalid")
            return None
        # Allowed requests are audited by the tool that runs (with what it returned); one
        # line per HTTP request here would only add noise. Denials are always audited.
        principal = getattr(user, "email", None) or user.id
        tenant = getattr(user, "tenant_id", "") or "default"
        return PrincipalAccessToken(
            token="",  # noqa: S106  intentionally not retained
            client_id=str(user.id),
            scopes=[],
            subject=str(user.id),
            principal=principal,
            groups=list(getattr(user, "group_ids", [])),
            tenant_id=tenant,
        )


def current_principal() -> PrincipalAccessToken:
    """The verified caller of the current MCP request. Raises if there is none, so a tool
    reached without transport authentication (a misconfigured server) fails closed."""
    token = get_access_token()
    if not isinstance(token, PrincipalAccessToken):
        raise PermissionError("authentication required")
    return token


def auth_settings_from_env() -> Any:
    """AuthSettings for a resource server MCP: the issuer that mints its tokens and its own
    public URL (advertised in the protected resource metadata). Fails fast if unset."""
    from mcp.server.auth.settings import AuthSettings
    from pydantic import AnyHttpUrl

    issuer = os.environ.get("OIDC_ISSUER_URL", "")
    resource = os.environ.get("PANSOPH_MCP_PUBLIC_URL", "")
    if not issuer or not resource:
        raise RuntimeError(
            "MCP requires authentication: set OIDC_ISSUER_URL and PANSOPH_MCP_PUBLIC_URL"
        )
    return AuthSettings(issuer_url=AnyHttpUrl(issuer), resource_server_url=AnyHttpUrl(resource))
