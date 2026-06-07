"""OIDC identity adapter. Validates an IdP bearer token and maps claims to a User.

Recommended default IdP is Zitadel; Keycloak, Authentik, and Ory also work. The
group claim is mapped to the user's group_ids so the same nested group machinery
serves human auth and connector ACLs. Token validation (JWKS, issuer, audience)
lands with the agora session layer; this is the adapter shape. Install
pansoph-identity[oidc].
"""

from __future__ import annotations

from pansoph_identity.port import User


class OIDCIdentity:
    def __init__(self, issuer_url: str, client_id: str, groups_claim: str = "groups") -> None:
        self._issuer_url = issuer_url
        self._client_id = client_id
        self._groups_claim = groups_claim

    async def get_user(self, user_id: str) -> User | None:
        raise NotImplementedError("OIDC user lookup lands with the agora session layer")

    async def groups_for_user(self, user_id: str) -> set[str]:
        raise NotImplementedError("OIDC group resolution lands with the agora session layer")

    async def authenticate(self, token: str) -> User | None:
        raise NotImplementedError("OIDC token validation lands with the agora session layer")
