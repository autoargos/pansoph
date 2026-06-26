"""Pure data shapes, ports, and exceptions for the auth subsystem.

Split out of `auth.py` so each file stays under the 500 LOC house limit, and so
the minters (`auth_minters.py`) import the shapes without importing the
`CredentialProvider`. Nothing here does I/O.

House rule: no em dashes anywhere in this file.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# 1. Exceptions
# ---------------------------------------------------------------------------
#
# Three distinct, narrow exception types so callers can react correctly:
#   - AuthError          generic auth failure (base type, catch-all)
#   - TokenRefreshError  the token endpoint refused or returned a bad response;
#                        usually transient or a config problem, worth surfacing.
#   - ReauthRequired     the durable credential itself is dead (an OAuth
#                        invalid_grant: the refresh token was revoked/expired, or
#                        the service-account key was disabled). No amount of
#                        retrying fixes this; a human must re-authorize.


class AuthError(Exception):
    """Base class for every error raised by the auth subsystem."""


class TokenRefreshError(AuthError):
    """Minting a fresh access token failed (bad response, network, parse).

    This is the recoverable bucket: the durable credential may still be valid,
    so a later retry can succeed.
    """


class ReauthRequired(AuthError):
    """The durable credential is dead and a human must re-authorize.

    Raised on an OAuth `invalid_grant` (revoked or expired refresh token, a
    disabled service-account key, a withdrawn consent). The platform should
    surface this to the operator rather than retry, because retrying cannot
    succeed.
    """


# ---------------------------------------------------------------------------
# 2. Pure data shapes (no I/O)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccessToken:
    """A short-lived bearer credential, minted and cached centrally.

    `expires_at` is always a timezone-aware UTC datetime. The minters convert a
    relative `expires_in` (seconds) into this absolute instant so the cache can
    compare against `datetime.now(UTC)` without any ambiguity.

    `endpoint` carries a source-discovered base URL when the grant returns one,
    most notably Salesforce's `instance_url`. It is part of the mint result and
    cached alongside the token so a connector does not need a second round trip
    to learn where to send API calls. It is None for grants that do not return
    one (the connector then uses its configured base URL).
    """

    value: str
    token_type: str = "Bearer"  # noqa: S105  the OAuth token type, not a secret
    expires_at: datetime | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)
    endpoint: str | None = None


class GrantType(StrEnum):
    """The OAuth grant a connector declares it needs.

    The string values match the wire `grant_type` where one exists, which keeps
    the declaration and the request body aligned and easy to audit.
    """

    REFRESH_TOKEN = "refresh_token"  # noqa: S105  grant-type value, not a secret (OAuth2 6749 s6)
    JWT_BEARER = "jwt_bearer"  # RFC 7523: Google SA/DWD, Salesforce JWT
    CLIENT_CREDENTIALS = "client_credentials"  # MS Graph app-only, SF client-creds
    STATIC = "static"  # long-lived PAT / API token, no token endpoint


@dataclass(frozen=True)
class SecretRef:
    """An opaque handle into the SecretStore. NOT the secret value.

    A connector's configuration carries these handles, never the plaintext.
    Secret material is resolved just-in-time at mint time and wiped after use
    (per the spec, section 2.3). Two parts: the tenant the secret belongs to and
    the store key.
    """

    tenant_id: str
    name: str


@dataclass(frozen=True)
class AuthRequirement:
    """What a connector DECLARES about its auth (data, not behavior).

    The platform reads this to know which grant to run, which token endpoint to
    hit, and which fields are secret (so it can externalize them into the
    SecretStore). `secret_refs` maps a logical name (for example "client_secret",
    "refresh_token", "private_key") to an opaque handle. `extra` holds non-secret
    parameters (client_id, iss, sub, aud, audience, tenant, instance, scope
    override, assertion_kid, assertion_ttl, ...).
    """

    grant: GrantType
    token_url: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    secret_refs: Mapping[str, SecretRef] = field(default_factory=dict)
    extra: Mapping[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        """Check the required secret_refs and extra keys exist for this grant.

        Raises `AuthError` with a precise message naming the missing field, so a
        misconfiguration fails fast and legibly rather than at the first mint.
        """
        required_secrets: tuple[str, ...]
        required_extra: tuple[str, ...]

        if self.grant is GrantType.REFRESH_TOKEN:
            # client_id is non-secret; client_secret and refresh_token are secret.
            required_secrets = ("client_secret", "refresh_token")
            required_extra = ("client_id",)
        elif self.grant is GrantType.CLIENT_CREDENTIALS:
            required_secrets = ("client_secret",)
            required_extra = ("client_id",)
        elif self.grant is GrantType.JWT_BEARER:
            # private_key (PEM) is secret; iss/sub/aud identify the assertion.
            required_secrets = ("private_key",)
            required_extra = ("iss", "sub", "aud")
        elif self.grant is GrantType.STATIC:
            # A long-lived token sitting in the store, used verbatim.
            required_secrets = ("token",)
            required_extra = ()
        else:  # pragma: no cover  exhaustive over the StrEnum
            raise AuthError(f"unknown grant type: {self.grant!r}")

        for name in required_secrets:
            if name not in self.secret_refs:
                raise AuthError(
                    f"grant {self.grant.value} requires secret_ref {name!r}, "
                    f"but it is missing from secret_refs"
                )
        for name in required_extra:
            if not self.extra.get(name):
                raise AuthError(
                    f"grant {self.grant.value} requires extra field {name!r}, "
                    f"but it is missing or empty in extra"
                )

        if self.grant is not GrantType.STATIC and not self.token_url:
            raise AuthError(f"grant {self.grant.value} requires a token_url")


# ---------------------------------------------------------------------------
# 3. Ports (structural protocols, no I/O assumptions baked in)
# ---------------------------------------------------------------------------


@runtime_checkable
class SecretStore(Protocol):
    """A local structural mirror of pansoph-infra's SecretStore port.

    Declared here so this package does NOT hard-depend on pansoph-infra. Any
    object with these three coroutine methods satisfies it, including the real
    envelope-encryption store and the in-memory fake the tests use. The shapes
    match pansoph_infra.ports.SecretStore exactly so an adapter is unnecessary.
    """

    async def set_secret(self, tenant_id: str, name: str, value: bytes) -> None:
        """Seal and store a secret for a tenant."""
        ...

    async def get_secret(self, tenant_id: str, name: str) -> bytes | None:
        """Return the unsealed secret, or None if absent."""
        ...

    async def delete_secret(self, tenant_id: str, name: str) -> None:
        """Delete a secret. Idempotent."""
        ...


@runtime_checkable
class TokenSource(Protocol):
    """What every connector depends on. Connectors NEVER hold raw secrets.

    A connector receives a `TokenSource` bound to its instance and calls
    `get_access_token()` per request. `force_refresh` bypasses the cache, which
    a caller uses after a 401 to mint a guaranteed-fresh token.
    """

    async def get_access_token(self, *, force_refresh: bool = False) -> AccessToken:
        """Return a valid access token, minting or refreshing as needed."""
        ...


# A "sleeper" is an injectable async sleep so tests can make backoff
# deterministic and instant. Production passes `asyncio.sleep`.
Sleeper = Callable[[float], Awaitable[None]]


# ---------------------------------------------------------------------------
# 4. Token-response helper (shared by the minters)
# ---------------------------------------------------------------------------


def expires_at_from_expires_in(expires_in: object, clock: Callable[[], datetime]) -> datetime:
    """Convert a token response `expires_in` (seconds) to an absolute UTC instant.

    Tolerant of the int/str variance real providers show (some send "3600",
    some send 3600). Falls back to a conservative 3600s when the field is
    missing or unparseable, so a token without a stated lifetime still expires
    and gets refreshed rather than being treated as eternal.
    """
    seconds = 3600.0
    if expires_in is not None:
        try:
            seconds = float(expires_in)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            seconds = 3600.0
    return clock() + timedelta(seconds=seconds)
