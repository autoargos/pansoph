"""OIDC identity adapter. Validates an IdP bearer token and maps claims to a User.

authenticate verifies the JWT signature against the issuer's JWKS (discovered from
the well-known configuration and cached), checks issuer, audience, and expiry, then
maps the subject, email, and groups claim to a User. The groups claim feeds the same
nested group machinery that connector ACLs use. get_user and groups_for_user read a
cache populated at authentication time, since a generic user lookup needs the IdP's
admin API or SCIM, which is a later addition.

Recommended default IdP is Zitadel; Keycloak, Authentik, and Ory also work. Install
pansoph-identity[oidc].
"""

from __future__ import annotations

from typing import Any

from pansoph_identity.port import User


class OIDCIdentity:
    def __init__(self, issuer_url: str, client_id: str, groups_claim: str = "groups") -> None:
        self._issuer_url = issuer_url.rstrip("/")
        self._client_id = client_id
        self._groups_claim = groups_claim
        self._jwks_client: Any = None
        self._cache: dict[str, User] = {}

    async def _jwks(self) -> Any:
        import httpx
        from jwt import PyJWKClient

        if self._jwks_client is None:
            url = f"{self._issuer_url}/.well-known/openid-configuration"
            async with httpx.AsyncClient(timeout=15.0) as client:
                conf = (await client.get(url)).json()
            self._jwks_client = PyJWKClient(conf["jwks_uri"])
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
            id=claims["sub"],
            email=claims.get("email"),
            display_name=claims.get("name"),
            group_ids=list(claims.get(self._groups_claim, [])),
        )
        self._cache[user.id] = user
        return user

    async def get_user(self, user_id: str) -> User | None:
        return self._cache.get(user_id)

    async def groups_for_user(self, user_id: str) -> set[str]:
        user = self._cache.get(user_id)
        return set(user.group_ids) if user else set()
