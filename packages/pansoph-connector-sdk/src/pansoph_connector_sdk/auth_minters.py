"""The three OAuth minters: REFRESH_TOKEN, CLIENT_CREDENTIALS, JWT_BEARER.

A "minter" is an async callable that resolves the secrets a grant needs, builds
the token request, POSTs it to the requirement's token endpoint, parses the
response into an `AccessToken` (converting the relative `expires_in` into an
absolute UTC `expires_at`), and returns it. The minters are the only code that
constructs a token-endpoint request, so every wire-level OAuth detail lives
here and nowhere else.

This file is split out of `auth.py` purely to respect the 500 LOC house limit.
It imports the pure data shapes and exceptions from `auth.py`; `auth.py` imports
the minters lazily from inside method bodies, so there is no import cycle at
module load time.

JWT assertions are signed RS256 with PyJWT (the `pyjwt[crypto]` extra), which is
already the workspace's JWT library (pansoph-identity, pansoph-core). PyJWT
produces a correct base64url `header.payload.signature` assertion, so we do not
hand-roll the JOSE encoding.

House rule: no em dashes anywhere in this file.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol

import httpx
import jwt

from .auth_types import (
    AccessToken,
    AuthRequirement,
    GrantType,
    ReauthRequired,
    SecretStore,
    TokenRefreshError,
    expires_at_from_expires_in,
)

# The maximum lifetime we will request for a self-signed JWT assertion. RFC 7523
# leaves this to the provider; Google caps service-account assertions at 3600s,
# Salesforce at 300s for the assertion's own `exp`. We default to 3600 and clamp
# down via `assertion_ttl` in `extra` when a provider needs less.
_DEFAULT_ASSERTION_TTL = 3600
_MIN_ASSERTION_TTL = 60


# A callback the provider hands to the refresh-token minter so a rotated token
# is persisted to the durable store (and mirrored in memory) BEFORE the mint
# returns. Awaitable so the store write is awaited inline.
SetRefreshToken = Callable[[str], Awaitable[None]]


class Minter(Protocol):
    """The shape every minter satisfies.

    The provider passes everything a minter could need as keyword arguments;
    each minter uses the subset its grant requires. Keeping a single signature
    lets `minter_for` return any of them behind one type.
    """

    async def __call__(
        self,
        *,
        requirement: AuthRequirement,
        tenant_id: str,
        secrets: SecretStore,
        http: httpx.AsyncClient,
        entry_refresh_token: str | None,
        clock: Callable[[], datetime],
        set_refresh_token: SetRefreshToken,
    ) -> AccessToken: ...


# ---------------------------------------------------------------------------
# Shared secret-resolution helper
# ---------------------------------------------------------------------------


async def _resolve_secret(
    secrets: SecretStore,
    requirement: AuthRequirement,
    logical_name: str,
) -> str:
    """Resolve one logical secret to its UTF-8 plaintext, just-in-time.

    The handle in `secret_refs` names the tenant and key inside the store, which
    may differ from the connector's own tenant (a shared org credential, say).
    Raises `ReauthRequired` if the secret is absent, because a missing durable
    secret is not a transient failure: a human must restore it.
    """
    ref = requirement.secret_refs[logical_name]
    raw = await secrets.get_secret(ref.tenant_id, ref.name)
    if raw is None:
        raise ReauthRequired(
            f"secret {logical_name!r} ({ref.tenant_id}/{ref.name}) is not in the "
            f"secret store; the credential must be re-provisioned"
        )
    return raw.decode("utf-8")


def _parse_token_response(
    response: httpx.Response,
    requirement: AuthRequirement,
    clock: Callable[[], datetime],
) -> tuple[dict[str, Any], AccessToken]:
    """Validate a token-endpoint response and build the base AccessToken.

    Returns the raw JSON body too so a grant-specific minter can pull extra
    fields (a rotated refresh_token, an instance_url). Distinguishes the OAuth
    error cases: `invalid_grant` means the durable credential is dead
    (`ReauthRequired`); any other non-2xx or unparseable body is a
    `TokenRefreshError` (recoverable).
    """
    if response.status_code >= 400:
        error_code = _oauth_error_code(response)
        if error_code == "invalid_grant":
            raise ReauthRequired(
                "the token endpoint returned invalid_grant; the refresh token or "
                "assertion credential is revoked or expired and must be re-authorized"
            )
        raise TokenRefreshError(
            f"token endpoint {requirement.token_url} returned "
            f"HTTP {response.status_code}: {response.text[:300]}"
        )

    try:
        body: dict[str, Any] = response.json()
    except ValueError as exc:
        raise TokenRefreshError(
            f"token endpoint {requirement.token_url} returned a non-JSON body"
        ) from exc

    access_value = body.get("access_token")
    if not isinstance(access_value, str) or not access_value:
        raise TokenRefreshError(
            f"token endpoint {requirement.token_url} response had no access_token"
        )

    token_type = body.get("token_type") or "Bearer"
    # `scope` comes back space-delimited per RFC 6749; fall back to the declared
    # scopes when the server echoes none.
    scope_field = body.get("scope")
    scopes = (
        frozenset(scope_field.split())
        if isinstance(scope_field, str) and scope_field
        else requirement.scopes
    )
    token = AccessToken(
        value=access_value,
        token_type=token_type,
        expires_at=expires_at_from_expires_in(body.get("expires_in"), clock),
        scopes=scopes,
        # Salesforce returns instance_url; other grants leave it None.
        endpoint=body.get("instance_url"),
    )
    return body, token


def _oauth_error_code(response: httpx.Response) -> str | None:
    """Pull the OAuth `error` field from a token-endpoint error body.

    RFC 6749 section 5.2 puts it in the JSON body as `{"error": "invalid_grant"}`.
    Lenient: returns None when the body is not JSON or has no error field.
    """
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, str) else None


# ---------------------------------------------------------------------------
# Minter: REFRESH_TOKEN (OAuth 2.0 RFC 6749 section 6)
# ---------------------------------------------------------------------------


async def mint_refresh_token(
    *,
    requirement: AuthRequirement,
    tenant_id: str,
    secrets: SecretStore,
    http: httpx.AsyncClient,
    entry_refresh_token: str | None,
    clock: Callable[[], datetime],
    set_refresh_token: SetRefreshToken,
) -> AccessToken:
    """Exchange a refresh token for a fresh access token.

    POSTs `grant_type=refresh_token` with the client_id (non-secret), the
    client_secret, and the refresh_token. If the response carries a NEW
    refresh_token (rotation, which Slack/Dropbox/Salesforce do), it is persisted
    via the SecretStore BEFORE this returns, or the connector locks itself out on
    the next run. The in-memory mirror is updated by the same callback.

    Uses the in-memory mirror (`entry_refresh_token`) when present to avoid a
    store read on the hot path, falling back to the durable store on a cold
    start.
    """
    client_secret = await _resolve_secret(secrets, requirement, "client_secret")
    # Prefer the in-memory mirror so a rotated token from this process is used
    # immediately; fall back to the durable store on a cold cache.
    refresh_token = entry_refresh_token
    if refresh_token is None:
        refresh_token = await _resolve_secret(secrets, requirement, "refresh_token")

    form = {
        "grant_type": "refresh_token",
        "client_id": requirement.extra["client_id"],
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    # Some providers want the scope echoed on refresh; include it when declared.
    if requirement.scopes:
        form["scope"] = " ".join(sorted(requirement.scopes))

    response = await http.post(requirement.token_url, data=form)
    body, token = _parse_token_response(response, requirement, clock)

    # Refresh-token rotation: if the provider issued a new refresh token, persist
    # it durably (and mirror it) before returning. Doing this after parsing but
    # before returning is the atomic-enough point: the access token is in hand,
    # and the durable write happens before any caller can use the result.
    rotated = body.get("refresh_token")
    if isinstance(rotated, str) and rotated and rotated != refresh_token:
        await set_refresh_token(rotated)

    return token


# ---------------------------------------------------------------------------
# Minter: CLIENT_CREDENTIALS (OAuth 2.0 RFC 6749 section 4.4)
# ---------------------------------------------------------------------------


async def mint_client_credentials(
    *,
    requirement: AuthRequirement,
    tenant_id: str,
    secrets: SecretStore,
    http: httpx.AsyncClient,
    entry_refresh_token: str | None,
    clock: Callable[[], datetime],
    set_refresh_token: SetRefreshToken,
) -> AccessToken:
    """Mint an app-only token with the client-credentials grant.

    POSTs `grant_type=client_credentials` with client_id + client_secret. There
    is no refresh token: when the token nears expiry the provider simply mints a
    new one.

    Scope handling: Microsoft Graph's app-only flow wants a single resource
    scope ending in `.default` (for example `https://graph.microsoft.com/.default`).
    A caller supplies that via `extra["scope"]`; otherwise we send the declared
    scopes space-joined, which is the generic RFC 6749 form.
    """
    client_secret = await _resolve_secret(secrets, requirement, "client_secret")

    form = {
        "grant_type": "client_credentials",
        "client_id": requirement.extra["client_id"],
        "client_secret": client_secret,
    }
    scope = requirement.extra.get("scope")
    if scope:
        # Explicit override, for example the Graph ".default" resource scope.
        form["scope"] = scope
    elif requirement.scopes:
        form["scope"] = " ".join(sorted(requirement.scopes))

    response = await http.post(requirement.token_url, data=form)
    _body, token = _parse_token_response(response, requirement, clock)
    return token


# ---------------------------------------------------------------------------
# Minter: JWT_BEARER (RFC 7523)
# ---------------------------------------------------------------------------


async def mint_jwt_bearer(
    *,
    requirement: AuthRequirement,
    tenant_id: str,
    secrets: SecretStore,
    http: httpx.AsyncClient,
    entry_refresh_token: str | None,
    clock: Callable[[], datetime],
    set_refresh_token: SetRefreshToken,
) -> AccessToken:
    """Mint a token via a self-signed JWT assertion (Google SA/DWD, Salesforce JWT).

    Builds a short-lived RS256-signed JWT from `extra` (iss, sub, aud) with
    `iat=now` and `exp=now+ttl` where ttl is clamped to the provider's maximum
    (default 3600s, overridable via `extra["assertion_ttl"]`, floored at 60s).
    Signs it with the PEM private key from the SecretStore, then POSTs
    `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer` with the assertion.

    There is no refresh token: re-mint on expiry. If the response carries an
    `instance_url` (Salesforce), it is captured on the returned AccessToken so
    the connector knows its API base.
    """
    private_key_pem = await _resolve_secret(secrets, requirement, "private_key")

    now = clock()
    ttl = _assertion_ttl(requirement)
    # JWT timestamps are NumericDate (seconds since epoch). PyJWT accepts
    # datetimes for iat/exp and converts them; we pass ints to be explicit and
    # provider-portable.
    issued_at = int(now.timestamp())
    expires = issued_at + ttl

    claims: dict[str, Any] = {
        "iss": requirement.extra["iss"],
        "sub": requirement.extra["sub"],
        "aud": requirement.extra["aud"],
        "iat": issued_at,
        "exp": expires,
    }
    # Google service accounts carry the requested scopes inside the assertion's
    # `scope` claim rather than as a token-request form field.
    if requirement.scopes:
        claims["scope"] = " ".join(sorted(requirement.scopes))

    # An optional key id goes in the JOSE header so the provider can select the
    # right public key (Salesforce connected-app certs, Google SA key ids).
    headers: dict[str, Any] = {}
    kid = requirement.extra.get("assertion_kid")
    if kid:
        headers["kid"] = kid

    try:
        assertion = jwt.encode(
            claims,
            private_key_pem,
            algorithm="RS256",
            headers=headers or None,
        )
    except Exception as exc:  # pragma: no cover  signing errors are config bugs
        # A bad PEM or unsupported key is not transient; the credential must be
        # fixed by an operator.
        raise ReauthRequired(
            f"failed to sign the JWT assertion (bad or unsupported private key): {exc}"
        ) from exc

    form = {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }
    response = await http.post(requirement.token_url, data=form)
    _body, token = _parse_token_response(response, requirement, clock)
    return token


def _assertion_ttl(requirement: AuthRequirement) -> int:
    """Resolve the assertion lifetime in seconds, clamped to a safe range.

    Defaults to 3600s. An operator can lower it (Salesforce caps the assertion
    `exp` at 300s) via `extra["assertion_ttl"]`. Floored at 60s so a typo cannot
    produce an already-expired assertion, and capped at the default so a typo
    cannot request an over-long one.
    """
    raw = requirement.extra.get("assertion_ttl")
    if raw is None:
        return _DEFAULT_ASSERTION_TTL
    try:
        ttl = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_ASSERTION_TTL
    return max(_MIN_ASSERTION_TTL, min(ttl, _DEFAULT_ASSERTION_TTL))


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


_MINTERS: dict[GrantType, Minter] = {
    GrantType.REFRESH_TOKEN: mint_refresh_token,
    GrantType.CLIENT_CREDENTIALS: mint_client_credentials,
    GrantType.JWT_BEARER: mint_jwt_bearer,
}


def minter_for(grant: GrantType) -> Minter:
    """Return the minter for a grant.

    STATIC has no token endpoint to mint against (the stored value is the token),
    so it is rejected here: a STATIC credential should be served directly from
    the store by the runtime, not minted. The other three map to their minters.
    """
    minter = _MINTERS.get(grant)
    if minter is None:
        raise TokenRefreshError(
            f"grant {grant.value} has no minter; STATIC tokens are served from the "
            f"secret store directly and are not minted at a token endpoint"
        )
    return minter
