"""The OAuthProvider seam: what every provider module implements.

An `OAuthProvider` is a small, mostly-declarative description of one identity
provider's authorization-code flow: where to send the user for consent, where to
exchange the returned code, which scopes to request by default, and whether the
provider supports PKCE. The four concrete providers (google, slack, atlassian,
github) are thin instances of this dataclass with a provider-specific token parse.

Client credentials (client_id / client_secret) are read from the environment per
provider, for example PANSOPH_OAUTH_GOOGLE_CLIENT_ID / _SECRET, with a SecretStore
fallback so an operator can keep them out of the process environment. Resolving
them is async (the store is async) and lives here so every provider inherits the
same lookup order.

References verified 2026-07-04 against each provider's current official OAuth docs;
the concrete endpoints live in the provider modules. This base module is pure aside
from the credential lookup and the token POST, both of which take injected
dependencies (a SecretStore, an httpx client) so tests stay hermetic.

No em dashes anywhere.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from ..errors import CodeExchangeError, ProviderNotConfiguredError

if TYPE_CHECKING:
    import httpx
    from pansoph_connector_sdk import SecretStore


@dataclass(frozen=True)
class TokenResult:
    """The outcome of a code exchange, the shape flow.py seals into the config.

    `access_token` is always present. `refresh_token` is present only when the
    provider issues one (offline access). `expires_in` is the provider-stated
    lifetime in seconds, carried through so a later refresh can be scheduled;
    `extra` keeps any provider-specific fields a connector may need (a Slack team
    id, an Atlassian cloud id) without widening this shape.
    """

    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None
    scope: str | None = None
    token_type: str = "Bearer"  # noqa: S105  the OAuth token type, not a secret
    extra: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OAuthProvider:
    """One identity provider's authorization-code flow, mostly declarative.

    A provider module builds one of these and exports it as `PROVIDER`. The
    registry collects them by `name`. Fields:

    - name: the URL slug (google, slack, atlassian, github).
    - authorize_url: the consent endpoint the user is redirected to.
    - token_url: the endpoint that exchanges a code (and later a refresh token).
    - default_scopes: requested when a connector does not override them.
    - uses_pkce: whether to generate and send an S256 PKCE challenge. True for
      providers that support it (defense in depth even for confidential clients).
    - scope_separator: how scopes are joined in the authorize URL (space per RFC
      6749; GitHub historically uses a comma, so it is configurable).
    - extra_authorize_params: static params every authorize URL carries (for
      example Google's access_type=offline, Atlassian's audience=api.atlassian.com).
    - env_prefix: the per-provider environment variable stem for credentials,
      for example GOOGLE gives PANSOPH_OAUTH_GOOGLE_CLIENT_ID / _SECRET.
    """

    name: str
    authorize_url: str
    token_url: str
    default_scopes: tuple[str, ...] = ()
    uses_pkce: bool = False
    scope_separator: str = " "
    extra_authorize_params: Mapping[str, str] = field(default_factory=dict)
    env_prefix: str = ""

    # -- credential resolution --------------------------------------------

    def _env_stem(self) -> str:
        """The environment variable stem for this provider's credentials."""
        return (self.env_prefix or self.name).upper()

    async def client_id(self, secrets_store: SecretStore | None = None) -> str:
        """Resolve the client id: environment first, then the secret store.

        Raises `ProviderNotConfiguredError` naming the expected env var when
        neither source has it, so an operator gets an actionable message rather
        than a silent empty redirect.
        """
        return await self._resolve_credential("CLIENT_ID", "client_id", secrets_store)

    async def client_secret(self, secrets_store: SecretStore | None = None) -> str:
        """Resolve the client secret: environment first, then the secret store."""
        return await self._resolve_credential("CLIENT_SECRET", "client_secret", secrets_store)

    async def _resolve_credential(
        self, env_suffix: str, store_key: str, secrets_store: SecretStore | None
    ) -> str:
        env_name = f"PANSOPH_OAUTH_{self._env_stem()}_{env_suffix}"
        value = os.getenv(env_name, "")
        if value:
            return value
        if secrets_store is not None:
            # Provider-level credentials are stored under a reserved tenant so they
            # are shared across tenants (one registered app per provider), never
            # colliding with a per-instance connector secret.
            raw = await secrets_store.get_secret("_oauth", f"{self.name}/{store_key}")
            if raw:
                return raw.decode("utf-8")
        raise ProviderNotConfiguredError(
            f"provider {self.name!r} is missing its {store_key}; set {env_name} "
            f"or store it under _oauth/{self.name}/{store_key} in the secret store"
        )

    # -- authorize URL ----------------------------------------------------

    def build_authorize_url(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        state: str,
        scopes: tuple[str, ...] | None = None,
        code_challenge: str | None = None,
    ) -> str:
        """Build the provider consent URL the user is redirected to (302).

        Uses the connector-declared `scopes` when given, otherwise the provider's
        `default_scopes`. Includes a PKCE S256 challenge when `uses_pkce` and a
        challenge is supplied. Merges the provider's static
        `extra_authorize_params` last so a provider requirement (audience,
        access_type) is always present.
        """
        chosen = scopes if scopes else self.default_scopes
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        if chosen:
            params["scope"] = self.scope_separator.join(chosen)
        if self.uses_pkce and code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        params.update(self.extra_authorize_params)
        return f"{self.authorize_url}?{urlencode(params)}"

    # -- code exchange ----------------------------------------------------

    async def exchange_code(
        self,
        *,
        http: httpx.AsyncClient,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
        code_verifier: str | None = None,
    ) -> TokenResult:
        """Exchange an authorization code for tokens at the provider token endpoint.

        POSTs the standard RFC 6749 authorization-code form. Sends the PKCE
        `code_verifier` when one was used. Delegates response parsing to
        `parse_token_response` so a provider with a non-standard body (Slack wraps
        the token in an envelope) can override just that. Raises
        `CodeExchangeError` on a non-2xx response, mirroring the connector rule
        that an upstream failure is surfaced, never swallowed.
        """
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if code_verifier:
            form["code_verifier"] = code_verifier
        response = await http.post(
            self.token_url, data=form, headers={"Accept": "application/json"}
        )
        if response.status_code >= 400:
            raise CodeExchangeError(
                f"provider {self.name!r} token endpoint returned "
                f"HTTP {response.status_code}: {response.text[:300]}"
            )
        return self.parse_token_response(response)

    async def refresh(
        self,
        *,
        http: httpx.AsyncClient,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> TokenResult:
        """Exchange a refresh token for a fresh access token (optional flow).

        Providers that issue refresh tokens (Google, Slack rotating tokens,
        Atlassian) can refresh out of band, but the running sync path refreshes
        through the connector's declared AuthRequirement and the shared
        CredentialProvider, so this is a convenience for a provider that needs it.
        """
        form = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        response = await http.post(
            self.token_url, data=form, headers={"Accept": "application/json"}
        )
        if response.status_code >= 400:
            raise CodeExchangeError(
                f"provider {self.name!r} refresh returned "
                f"HTTP {response.status_code}: {response.text[:300]}"
            )
        return self.parse_token_response(response)

    def parse_token_response(self, response: httpx.Response) -> TokenResult:
        """Parse a standard RFC 6749 JSON token response into a TokenResult.

        The default handles the common OAuth 2.0 shape (access_token,
        refresh_token, expires_in, scope). A provider whose body differs overrides
        this in its own module (Slack nests the token, GitHub can return form
        encoding). Raises `CodeExchangeError` on a missing access token so a
        malformed 200 is not treated as success.
        """
        try:
            body = response.json()
        except ValueError as exc:
            raise CodeExchangeError(
                f"provider {self.name!r} returned a non-JSON token body"
            ) from exc
        return token_result_from_standard_body(self.name, body)


def token_result_from_standard_body(provider: str, body: object) -> TokenResult:
    """Build a TokenResult from a decoded standard token response body.

    Shared by the base parser and any provider override that first unwraps an
    envelope down to the standard fields. Raises `CodeExchangeError` when there is
    no access token, so a provider error body never seals an empty credential.
    """
    if not isinstance(body, dict):
        raise CodeExchangeError(f"provider {provider!r} token response was not an object")
    access = body.get("access_token")
    if not isinstance(access, str) or not access:
        error = body.get("error") or body.get("error_description")
        raise CodeExchangeError(
            f"provider {provider!r} token response had no access_token"
            + (f": {error}" if error else "")
        )
    return TokenResult(
        access_token=access,
        refresh_token=_str_or_none(body.get("refresh_token")),
        expires_in=_int_or_none(body.get("expires_in")),
        scope=_str_or_none(body.get("scope")),
        token_type=str(body.get("token_type") or "Bearer"),
    )


def _int_or_none(value: object) -> int | None:
    """Parse a token lifetime that may arrive as an int or a numeric string."""
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


# ---------------------------------------------------------------------------
# PKCE helpers (RFC 7636). Pure, so a provider or the flow can generate a pair.
# ---------------------------------------------------------------------------


def new_pkce_verifier() -> str:
    """Return a fresh high-entropy PKCE code verifier (RFC 7636 section 4.1)."""
    return secrets.token_urlsafe(64)


def pkce_challenge_s256(verifier: str) -> str:
    """Derive the S256 code challenge for a verifier (RFC 7636 section 4.2).

    base64url(SHA256(verifier)) with the padding stripped, which is the exact
    encoding the spec requires.
    """
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
