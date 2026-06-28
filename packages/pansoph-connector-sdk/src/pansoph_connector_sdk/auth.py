"""The auto-refresh auth subsystem: the caching, single-flight CredentialProvider.

This module is the single, provider-agnostic place where short-lived bearer
credentials are minted, cached, and refreshed. Connectors never see raw secrets.
They hold a `TokenSource` and call `get_access_token()` per request. The
`CredentialProvider` is the only component that touches secret material and the
token endpoints, so the blast radius of a credential leak is one class.

Design references (per the spec, section 2): OAuth 2.0 (RFC 6749), the JWT
bearer grant (RFC 7523), Google/Microsoft/Salesforce production auth flows, and
the Airbyte/Meltano secret-hydration and Authenticator patterns.

The pure data shapes, ports, and exceptions live in `auth_types.py`; the three
grant implementations (the "minters") live in `auth_minters.py`. This split
keeps every file under the 500 LOC house limit and lets the minters import the
shapes without importing this provider. This file owns the caching/single-flight
`CredentialProvider`, the per-credential cache, the bound `TokenSource`, and the
on-the-wire retry helpers.

House rule: no em dashes anywhere in this file.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from .auth_types import (
    AccessToken,
    AuthError,
    AuthRequirement,
    GrantType,
    ReauthRequired,
    SecretStore,
    Sleeper,
    TokenSource,
)

if TYPE_CHECKING:
    # Imported only for type checking to avoid a runtime import cycle: the
    # minters import the data shapes from auth_types, and this module references
    # the minters only from inside method bodies.
    from .auth_minters import Minter


# ---------------------------------------------------------------------------
# 1. The per-credential cache entry
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    """Mutable per-credential cache slot guarded by its own single-flight lock.

    The `lock` serializes refreshes for one credential key so that N concurrent
    callers trigger exactly one mint (the single-flight pattern). `refresh_token`
    is an in-memory mirror of the rotating refresh token: the authoritative copy
    is in the SecretStore, but mirroring it lets a refresh proceed without a
    store read on the hot path.
    """

    token: AccessToken | None = None
    refresh_token: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# ---------------------------------------------------------------------------
# 2. The bound TokenSource
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _BoundTokenSource:
    """A `TokenSource` bound to one (tenant, requirement) pair.

    It holds no secrets and no token state. Every call delegates back into the
    provider, which owns the cache and the single-flight lock. This is the
    object a connector actually holds.
    """

    _provider: CredentialProvider
    _tenant_id: str
    _requirement: AuthRequirement

    async def get_access_token(self, *, force_refresh: bool = False) -> AccessToken:
        return await self._provider._get_token(
            self._tenant_id, self._requirement, force_refresh=force_refresh
        )


# ---------------------------------------------------------------------------
# 3. The central CredentialProvider
# ---------------------------------------------------------------------------


class CredentialProvider:
    """Owns the token cache and the refresh logic for every credential.

    This is the only component that resolves secrets and calls token endpoints.
    Connectors get a `TokenSource` from `token_source()` and never touch this
    class directly after wiring.

    Concurrency model: an `asyncio.Lock` per credential key gives in-process
    single-flight. For a multi-instance deployment a distributed advisory lock
    (Postgres `pg_advisory_lock` or a Valkey lock) would replace the in-process
    lock so two agora instances do not both mint and, worse, both rotate a
    refresh token. That distributed lock is out of scope for this phase; the
    seam is the `_entries` map below. See the spec, section 2.2.
    """

    def __init__(
        self,
        secret_store: SecretStore,
        http_client: httpx.AsyncClient,
        *,
        refresh_skew_seconds: int = 300,
        sleeper: Sleeper | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Build a provider.

        Args:
            secret_store: where secret material lives. Resolved just-in-time.
            http_client: an injected httpx client (injectable so tests mock it
                with respx and so the platform can configure egress allowlists,
                timeouts, and TLS once, centrally).
            refresh_skew_seconds: refresh proactively this many seconds before
                the token actually expires, so an in-flight request never races
                expiry. Default 300s per the spec.
            sleeper: async sleep used by the retry/backoff helper. Injectable so
                tests run instantly and deterministically. Defaults to
                asyncio.sleep.
            clock: returns "now" as a timezone-aware UTC datetime. Injectable so
                tests can pin time and exercise proactive refresh without
                waiting. Defaults to a real UTC clock.
        """
        from datetime import UTC

        self._secrets = secret_store
        self._http = http_client
        self._skew = refresh_skew_seconds
        self._sleeper: Sleeper = sleeper or asyncio.sleep
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        # Credential key -> cache entry. The key is stable across calls so the
        # same credential always lands on the same lock and the same token.
        self._entries: dict[str, _CacheEntry] = {}
        # Guards creation of cache entries themselves (the map mutation), which
        # is distinct from the per-credential refresh lock inside each entry.
        self._entries_guard = asyncio.Lock()

    # -- public API --------------------------------------------------------

    def token_source(self, tenant_id: str, requirement: AuthRequirement) -> TokenSource:
        """Return a `TokenSource` bound to this tenant and requirement.

        The requirement is validated eagerly so a misconfiguration surfaces at
        wiring time, not at the first sync.
        """
        requirement.validate()
        return _BoundTokenSource(self, tenant_id, requirement)

    async def get_endpoint(self, tenant_id: str, requirement: AuthRequirement) -> str | None:
        """Return the source-discovered endpoint for this credential, if any.

        Salesforce returns an `instance_url` from the token mint that the
        connector must use as its API base. This forces a mint if the token is
        not cached, then returns the cached `endpoint`. Returns None for grants
        that do not discover an endpoint.
        """
        token = await self._get_token(tenant_id, requirement)
        return token.endpoint

    # -- the cache + single-flight core ------------------------------------

    @staticmethod
    def _credential_key(tenant_id: str, requirement: AuthRequirement) -> str:
        """Derive a stable string key from (tenant_id, requirement identity).

        Identity is the grant, the token endpoint, and the sorted secret-ref
        handles. Two requirements that resolve the same secrets against the same
        endpoint share a cache slot; anything that differs gets its own. The key
        deliberately excludes `extra` values like a one-off assertion_ttl that do
        not change which credential is in play, but includes the secret-ref names
        and handles, which do.
        """
        refs = ",".join(
            f"{logical}={ref.tenant_id}/{ref.name}"
            for logical, ref in sorted(requirement.secret_refs.items())
        )
        return f"{tenant_id}|{requirement.grant.value}|{requirement.token_url}|{refs}"

    async def _entry_for(self, key: str) -> _CacheEntry:
        """Fetch or lazily create the cache entry for a key, race-free."""
        entry = self._entries.get(key)
        if entry is not None:
            return entry
        # Double-checked creation under the map guard so two callers do not
        # create two entries (and therefore two independent locks) for one key.
        async with self._entries_guard:
            entry = self._entries.get(key)
            if entry is None:
                entry = _CacheEntry()
                self._entries[key] = entry
            return entry

    def _is_fresh(self, token: AccessToken | None) -> bool:
        """True while now < expires_at - skew (the proactive-refresh window)."""
        if token is None or token.expires_at is None:
            return False
        deadline = token.expires_at.timestamp() - self._skew
        return self._clock().timestamp() < deadline

    async def _get_token(
        self,
        tenant_id: str,
        requirement: AuthRequirement,
        *,
        force_refresh: bool = False,
    ) -> AccessToken:
        """Return a valid token, using the cache and single-flight refresh.

        Fast path: return the cached token while it is outside the skew window
        and the caller did not force a refresh. Slow path: take the per-key lock,
        double-check (another waiter may have just refreshed), then mint.
        """
        key = self._credential_key(tenant_id, requirement)
        entry = await self._entry_for(key)

        # Fast path, no lock. A torn read of `entry.token` is fine here: the
        # worst case is we needlessly take the lock and double-check. The local
        # `is not None` check narrows the type without a production assert.
        cached = entry.token
        if not force_refresh and cached is not None and self._is_fresh(cached):
            return cached

        # Slow path. Single-flight: only one coroutine per key mints at a time.
        async with entry.lock:
            # Double-check: a coroutine that was queued on the lock may now find
            # a freshly minted token and avoid a second, redundant mint.
            cached = entry.token
            if not force_refresh and cached is not None and self._is_fresh(cached):
                return cached

            token = await self._mint(tenant_id, requirement, entry)
            entry.token = token
            return token

    async def _mint(
        self,
        tenant_id: str,
        requirement: AuthRequirement,
        entry: _CacheEntry,
    ) -> AccessToken:
        """Dispatch to the right minter for the grant.

        Imported lazily to avoid an import cycle (the minters import the data
        shapes from auth_types). The minter resolves secrets just-in-time, builds
        and POSTs the token request, and returns an `AccessToken`.
        """
        if requirement.grant is GrantType.STATIC:
            # A STATIC grant has no token endpoint: the stored value is the token.
            return await self._static_token(requirement)

        from .auth_minters import minter_for

        minter: Minter = minter_for(requirement.grant)
        return await minter(
            requirement=requirement,
            tenant_id=tenant_id,
            secrets=self._secrets,
            http=self._http,
            entry_refresh_token=entry.refresh_token,
            clock=self._clock,
            set_refresh_token=lambda value: self._store_rotated_refresh_token(
                tenant_id, requirement, entry, value
            ),
        )

    async def _static_token(self, requirement: AuthRequirement) -> AccessToken:
        """Serve a long-lived token (a PAT or API token) straight from the store.

        A STATIC grant has no token endpoint: the stored value IS the bearer token
        (a github fine-grained PAT, an Atlassian API token, a Notion internal token).
        It is cached with a far-future expiry so the hot path serves it without
        re-resolving; a connector that rotates the token re-provisions it in the
        store, and the next sync picks up the new value when the cache lapses.
        """
        ref = requirement.secret_refs["token"]
        raw = await self._secrets.get_secret(ref.tenant_id, ref.name)
        if raw is None:
            raise ReauthRequired(
                f"static token {ref.tenant_id}/{ref.name} is not in the secret store"
            )
        return AccessToken(
            value=raw.decode("utf-8"),
            expires_at=self._clock() + timedelta(days=3650),
        )

    async def _store_rotated_refresh_token(
        self,
        tenant_id: str,
        requirement: AuthRequirement,
        entry: _CacheEntry,
        new_value: str,
    ) -> None:
        """Persist a rotated refresh token atomically, then mirror in memory.

        Order matters: persist to the durable store BEFORE the mint returns and
        before we update the in-memory mirror. If the process dies after a
        rotation but before the store write, the old refresh token is already
        invalid at the provider and the connector is locked out. Writing the
        store first closes that window as much as a single store can.
        """
        ref = requirement.secret_refs["refresh_token"]
        await self._secrets.set_secret(ref.tenant_id, ref.name, new_value.encode("utf-8"))
        entry.refresh_token = new_value

    async def invalidate(self, tenant_id: str, requirement: AuthRequirement) -> None:
        """Drop the cached token for a credential so the next call re-mints.

        Used by the 401 refresh-and-retry path: when the server says the token
        is no longer valid, we clear the cache and force a fresh mint.
        """
        key = self._credential_key(tenant_id, requirement)
        entry = self._entries.get(key)
        if entry is not None:
            entry.token = None

    # -- on-the-wire retry helpers -----------------------------------------

    async def request(
        self,
        tenant_id: str,
        requirement: AuthRequirement,
        method: str,
        url: str,
        *,
        max_retries: int = 3,
        headers: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send an authenticated request, refreshing on 401 and backing off on 429.

        The access token is authoritative on the wire (spec, section 2.2):

        - On HTTP 401 with `WWW-Authenticate: ... error="invalid_token"`, the
          token is stale. Invalidate the cache and refresh-and-retry exactly
          once. A second 401 is a real failure (wrong scopes, wrong audience).
        - On HTTP 401 with `error="invalid_grant"`, the durable credential is
          dead. Raise `ReauthRequired` immediately; retrying cannot help.
        - On HTTP 429, honor `Retry-After` if present, otherwise exponential
          backoff, up to `max_retries`.

        This is the minimal reusable wrapper; a connector may call it directly or
        replicate just the parts it needs. The token is injected as a Bearer
        header; callers pass body/params via kwargs.
        """
        attempt = 0
        refreshed_on_401 = False
        while True:
            token = await self._get_token(tenant_id, requirement)
            request_headers = dict(headers or {})
            request_headers["Authorization"] = f"{token.token_type} {token.value}"
            response = await self._http.request(method, url, headers=request_headers, **kwargs)

            if response.status_code == 401:
                error = _parse_www_authenticate_error(response)
                if error == "invalid_grant":
                    # The credential itself is dead. No retry can fix this.
                    raise ReauthRequired(
                        "the source rejected the credential with invalid_grant; "
                        "the refresh credential is revoked or expired and must be "
                        "re-authorized by an operator"
                    )
                if not refreshed_on_401:
                    # invalid_token (or an unspecified 401): the access token is
                    # stale. Force exactly one refresh-and-retry.
                    refreshed_on_401 = True
                    await self.invalidate(tenant_id, requirement)
                    await self._get_token(tenant_id, requirement, force_refresh=True)
                    continue
                # Already refreshed once and still 401: a real authz failure.
                raise AuthError(
                    f"request to {url} still returned 401 after a token refresh; "
                    f"the token lacks the required scope or audience"
                )

            if response.status_code == 429 and attempt < max_retries:
                delay = _retry_after_seconds(response, attempt)
                await self._sleeper(delay)
                attempt += 1
                continue

            return response


# ---------------------------------------------------------------------------
# 4. Module-level wire helpers (pure, no provider state)
# ---------------------------------------------------------------------------


def _parse_www_authenticate_error(response: httpx.Response) -> str | None:
    """Extract the OAuth `error` token from a `WWW-Authenticate` header.

    RFC 6750 section 3 puts the error in the challenge as `error="invalid_token"`.
    Returns the bare error code (without quotes) or None if no challenge/error is
    present. Parsing is intentionally lenient: the header format varies by
    provider, so we scan for `error=` and read the quoted or bare value.
    """
    header: str | None = response.headers.get("WWW-Authenticate")
    if not header:
        return None
    marker = "error="
    idx = header.find(marker)
    if idx == -1:
        return None
    rest = header[idx + len(marker) :].lstrip()
    if rest.startswith('"'):
        end = rest.find('"', 1)
        return rest[1:end] if end != -1 else rest[1:]
    # Bare token: read up to the next separator.
    end = len(rest)
    for sep in (" ", ",", ";"):
        pos = rest.find(sep)
        if pos != -1:
            end = min(end, pos)
    return rest[:end] or None


def _retry_after_seconds(response: httpx.Response, attempt: int) -> float:
    """Compute the 429 backoff delay.

    Honors a `Retry-After` header when present (RFC 7231 gives it as either an
    integer seconds count or an HTTP-date; we support the seconds form, which is
    what these APIs send). Falls back to exponential backoff (1, 2, 4, ...
    seconds, capped) when the header is absent or unparseable.
    """
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return max(0.0, float(retry_after.strip()))
        except ValueError:
            # An HTTP-date form is rare for these APIs; fall through to backoff
            # rather than parse a date here.
            pass
    # Exponential backoff with a sane cap so a confused server cannot wedge us.
    return float(min(2**attempt, 60))
