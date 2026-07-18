"""OIDC identity adapter. Validates an IdP bearer token and maps claims to a User.

authenticate verifies the JWT signature against the issuer's JWKS, checks issuer and
audience, and maps the username and groups claim to a User. The groups claim feeds
the same nested-group machinery that connector ACLs use.

Networking note: in a container, the public issuer URL (what the browser and the
token's `iss` use) is often not reachable from the server. So a jwks_url override
lets agora fetch the keys over the internal network while still validating the token
against the public issuer.

Recommended default IdP is Zitadel; Keycloak (used in the demo), Authentik, and Ory
also work. Install pansoph-identity[oidc].
"""

from __future__ import annotations

from typing import Any

from pansoph_identity.port import User


class OIDCIdentity:
    def __init__(
        self,
        issuer_url: str,
        client_id: str,
        groups_claim: str = "groups",
        jwks_url: str = "",
        roles_claim: str = "roles",
        tenant_claim: str = "tenant",
    ) -> None:
        self._issuer_url = issuer_url.rstrip("/")
        self._client_id = client_id
        self._groups_claim = groups_claim
        self._jwks_url = jwks_url
        self._roles_claim = roles_claim
        # The token claim that carries the user's tenant. The retrieval path binds the
        # tenant to this (never the request body), so a multi-tenant IdP must emit it. A
        # token with no such claim maps to the single "default" tenant.
        self._tenant_claim = tenant_claim
        self._jwks_client: Any = None
        self._cache: dict[str, User] = {}

    async def _jwks(self) -> Any:
        from jwt import PyJWKClient

        if self._jwks_client is None:
            if self._jwks_url:
                url = self._jwks_url
            else:
                import httpx

                async with httpx.AsyncClient(timeout=15.0) as client:
                    conf = (
                        await client.get(f"{self._issuer_url}/.well-known/openid-configuration")
                    ).json()
                url = conf["jwks_uri"]
            self._jwks_client = PyJWKClient(url)
        return self._jwks_client

    async def authenticate(self, token: str) -> User | None:
        import jwt

        jwks = await self._jwks()
        try:
            signing_key = jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                audience=self._client_id,
                issuer=self._issuer_url,
            )
        except jwt.PyJWTError:
            return None
        user = User(
            id=claims.get("preferred_username") or claims["sub"],
            email=claims.get("email"),
            display_name=claims.get("name") or claims.get("preferred_username"),
            tenant_id=str(claims.get(self._tenant_claim) or "") or "default",
            group_ids=list(claims.get(self._groups_claim, [])),
            roles=list(claims.get(self._roles_claim, [])),
        )
        self._cache[user.id] = user
        return user

    async def get_user(self, user_id: str) -> User | None:
        return self._cache.get(user_id)

    async def groups_for_user(self, user_id: str) -> set[str]:
        user = self._cache.get(user_id)
        return set(user.group_ids) if user else set()
