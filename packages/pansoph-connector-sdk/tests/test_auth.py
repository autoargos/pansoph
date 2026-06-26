"""Hermetic tests for the auto-refresh auth subsystem.

Every token endpoint is mocked with respx, so no test touches the network. Time
is pinned via an injected clock so proactive-refresh and expiry behavior is
deterministic, and the backoff sleeper is injected so the 429 path runs
instantly. The SecretStore is an in-memory dict-backed fake implementing the
port (set_secret/get_secret/delete_secret).

House rule: no em dashes anywhere in this file.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pansoph_connector_sdk import (
    AccessToken,
    AuthRequirement,
    CredentialProvider,
    GrantType,
    ReauthRequired,
    SecretRef,
)

TOKEN_URL = "https://idp.example.com/oauth/token"
TENANT = "tenant-a"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeSecretStore:
    """A dict-backed SecretStore implementing the pansoph-infra port shape.

    Keyed by (tenant_id, name) so rotation tests can assert the stored bytes
    actually change. `set_calls` records writes so a test can prove a rotated
    refresh token was persisted.
    """

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], bytes] = {}
        self.set_calls: list[tuple[str, str, bytes]] = []

    async def set_secret(self, tenant_id: str, name: str, value: bytes) -> None:
        self._data[(tenant_id, name)] = value
        self.set_calls.append((tenant_id, name, value))

    async def get_secret(self, tenant_id: str, name: str) -> bytes | None:
        return self._data.get((tenant_id, name))

    async def delete_secret(self, tenant_id: str, name: str) -> None:
        self._data.pop((tenant_id, name), None)


class FixedClock:
    """A mutable clock so a test can advance time across the skew window."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class RecordingSleeper:
    """An async sleeper that records delays instead of waiting."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _provider(
    store: FakeSecretStore,
    *,
    clock: FixedClock | None = None,
    sleeper: RecordingSleeper | None = None,
    skew: int = 300,
) -> tuple[CredentialProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(base_url="https://idp.example.com")
    provider = CredentialProvider(
        store,
        client,
        refresh_skew_seconds=skew,
        sleeper=sleeper,
        clock=clock,
    )
    return provider, client


def _decode_segments(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Decode a JWT's header and payload without verifying, for shape assertions."""
    header_b64, payload_b64, _sig = token.split(".")

    def _pad(segment: str) -> bytes:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))

    return json.loads(_pad(header_b64)), json.loads(_pad(payload_b64))


# ---------------------------------------------------------------------------
# REFRESH_TOKEN: minting, caching, proactive refresh, force_refresh, rotation
# ---------------------------------------------------------------------------


@respx.mock
async def test_refresh_token_mints_valid_access_token() -> None:
    store = FakeSecretStore()
    await store.set_secret(TENANT, "client_secret", b"cs")
    await store.set_secret(TENANT, "refresh_token", b"rt-original")
    clock = FixedClock(datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC))
    provider, client = _provider(store, clock=clock)

    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "at-1",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "read write",
            },
        )
    )

    req = AuthRequirement(
        grant=GrantType.REFRESH_TOKEN,
        token_url=TOKEN_URL,
        scopes=frozenset({"read"}),
        secret_refs={
            "client_secret": SecretRef(TENANT, "client_secret"),
            "refresh_token": SecretRef(TENANT, "refresh_token"),
        },
        extra={"client_id": "cid"},
    )
    src = provider.token_source(TENANT, req)
    token = await src.get_access_token()

    assert isinstance(token, AccessToken)
    assert token.value == "at-1"
    assert token.token_type == "Bearer"
    # expires_at is now + expires_in, timezone-aware UTC.
    assert token.expires_at == clock.now + timedelta(seconds=3600)
    assert token.expires_at.tzinfo is not None
    assert token.scopes == frozenset({"read", "write"})
    assert route.call_count == 1
    await client.aclose()


@respx.mock
async def test_caching_within_skew_hits_endpoint_once() -> None:
    store = FakeSecretStore()
    await store.set_secret(TENANT, "client_secret", b"cs")
    await store.set_secret(TENANT, "refresh_token", b"rt")
    clock = FixedClock(datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC))
    provider, client = _provider(store, clock=clock)

    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
    )
    req = _refresh_req()
    src = provider.token_source(TENANT, req)

    first = await src.get_access_token()
    second = await src.get_access_token()

    # Both calls within the skew window return the SAME cached token, one mint.
    assert first.value == second.value == "at"
    assert route.call_count == 1
    await client.aclose()


@respx.mock
async def test_proactive_refresh_within_skew_remints() -> None:
    store = FakeSecretStore()
    await store.set_secret(TENANT, "client_secret", b"cs")
    await store.set_secret(TENANT, "refresh_token", b"rt")
    clock = FixedClock(datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC))
    provider, client = _provider(store, clock=clock, skew=300)

    values = iter(["at-1", "at-2"])
    respx.post(TOKEN_URL).mock(
        side_effect=lambda request: httpx.Response(
            200, json={"access_token": next(values), "expires_in": 3600}
        )
    )
    req = _refresh_req()
    src = provider.token_source(TENANT, req)

    first = await src.get_access_token()
    assert first.value == "at-1"

    # Advance to inside the skew window: token expires at +3600s, skew is 300s,
    # so at +3350s it is within skew and must re-mint proactively.
    clock.now = clock.now + timedelta(seconds=3350)
    second = await src.get_access_token()
    assert second.value == "at-2"
    await client.aclose()


@respx.mock
async def test_force_refresh_bypasses_cache() -> None:
    store = FakeSecretStore()
    await store.set_secret(TENANT, "client_secret", b"cs")
    await store.set_secret(TENANT, "refresh_token", b"rt")
    clock = FixedClock(datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC))
    provider, client = _provider(store, clock=clock)

    values = iter(["at-1", "at-2"])
    route = respx.post(TOKEN_URL).mock(
        side_effect=lambda request: httpx.Response(
            200, json={"access_token": next(values), "expires_in": 3600}
        )
    )
    req = _refresh_req()
    src = provider.token_source(TENANT, req)

    first = await src.get_access_token()
    forced = await src.get_access_token(force_refresh=True)

    assert first.value == "at-1"
    assert forced.value == "at-2"
    assert route.call_count == 2
    await client.aclose()


@respx.mock
async def test_refresh_token_rotation_is_persisted_and_reused() -> None:
    store = FakeSecretStore()
    await store.set_secret(TENANT, "client_secret", b"cs")
    await store.set_secret(TENANT, "refresh_token", b"rt-original")
    clock = FixedClock(datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC))
    provider, client = _provider(store, clock=clock)

    # First mint rotates the refresh token; capture the form the second mint sends.
    sent_refresh_tokens: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        body = dict(httpx.QueryParams(request.content.decode()))
        sent_refresh_tokens.append(body["refresh_token"])
        # The first request rotates; the second should carry the rotated token.
        new_rt = "rt-rotated" if body["refresh_token"] == "rt-original" else "rt-rotated-2"
        return httpx.Response(
            200,
            json={"access_token": "at", "expires_in": 3600, "refresh_token": new_rt},
        )

    respx.post(TOKEN_URL).mock(side_effect=_handler)
    req = _refresh_req()
    src = provider.token_source(TENANT, req)

    await src.get_access_token()
    # The rotated token was persisted to the store BEFORE the mint returned.
    stored = await store.get_secret(TENANT, "refresh_token")
    assert stored == b"rt-rotated"
    assert any(name == "refresh_token" for _t, name, _v in store.set_calls)

    # The next refresh uses the rotated token (from the in-memory mirror).
    await src.get_access_token(force_refresh=True)
    assert sent_refresh_tokens == ["rt-original", "rt-rotated"]
    await client.aclose()


# ---------------------------------------------------------------------------
# CLIENT_CREDENTIALS
# ---------------------------------------------------------------------------


@respx.mock
async def test_client_credentials_mints_with_default_scope() -> None:
    store = FakeSecretStore()
    await store.set_secret(TENANT, "client_secret", b"cs")
    clock = FixedClock(datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC))
    provider, client = _provider(store, clock=clock)

    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json={"access_token": "graph-at", "expires_in": 3599})

    respx.post(TOKEN_URL).mock(side_effect=_handler)

    req = AuthRequirement(
        grant=GrantType.CLIENT_CREDENTIALS,
        token_url=TOKEN_URL,
        secret_refs={"client_secret": SecretRef(TENANT, "client_secret")},
        extra={"client_id": "cid", "scope": "https://graph.microsoft.com/.default"},
    )
    token = await provider.token_source(TENANT, req).get_access_token()

    assert token.value == "graph-at"
    assert token.expires_at == clock.now + timedelta(seconds=3599)
    assert captured["grant_type"] == "client_credentials"
    assert captured["scope"] == "https://graph.microsoft.com/.default"
    await client.aclose()


# ---------------------------------------------------------------------------
# JWT_BEARER
# ---------------------------------------------------------------------------


def _rsa_keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


@respx.mock
async def test_jwt_bearer_posts_signed_assertion_and_captures_instance_url() -> None:
    private_pem, public_pem = _rsa_keypair()
    store = FakeSecretStore()
    await store.set_secret(TENANT, "private_key", private_pem.encode("utf-8"))
    clock = FixedClock(datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC))
    provider, client = _provider(store, clock=clock)

    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(
            200,
            json={
                "access_token": "sf-at",
                "expires_in": 3600,
                "instance_url": "https://my.salesforce.com",
            },
        )

    respx.post(TOKEN_URL).mock(side_effect=_handler)

    req = AuthRequirement(
        grant=GrantType.JWT_BEARER,
        token_url=TOKEN_URL,
        scopes=frozenset({"api"}),
        secret_refs={"private_key": SecretRef(TENANT, "private_key")},
        extra={
            "iss": "client-id",
            "sub": "user@example.com",
            "aud": "https://login.salesforce.com",
            "assertion_kid": "key-1",
        },
    )
    token = await provider.token_source(TENANT, req).get_access_token()

    assert token.value == "sf-at"
    # instance_url (Salesforce) is captured on the token and via get_endpoint.
    assert token.endpoint == "https://my.salesforce.com"
    assert await provider.get_endpoint(TENANT, req) == "https://my.salesforce.com"

    assert captured["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    assertion = captured["assertion"]
    # Three base64url segments: a well-formed JWS compact serialization.
    assert assertion.count(".") == 2

    header, payload = _decode_segments(assertion)
    assert header["alg"] == "RS256"
    assert header["kid"] == "key-1"
    assert payload["iss"] == "client-id"
    assert payload["sub"] == "user@example.com"
    assert payload["aud"] == "https://login.salesforce.com"
    assert payload["scope"] == "api"

    # The signature verifies against the public key (proves correct RS256 signing).
    verified = jwt.decode(
        assertion,
        public_pem,
        algorithms=["RS256"],
        audience="https://login.salesforce.com",
        # The assertion's exp is built from the test's fixed clock (in the past
        # relative to wall time), so skip exp validation here. This test verifies
        # the signature and claims, not the assertion's current liveness.
        options={"verify_exp": False},
    )
    assert verified["iss"] == "client-id"
    await client.aclose()


# ---------------------------------------------------------------------------
# On-the-wire: 401 refresh-and-retry, invalid_grant, 429 backoff
# ---------------------------------------------------------------------------


@respx.mock
async def test_401_invalid_token_triggers_one_refresh_and_retry() -> None:
    store = FakeSecretStore()
    await store.set_secret(TENANT, "client_secret", b"cs")
    await store.set_secret(TENANT, "refresh_token", b"rt")
    clock = FixedClock(datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC))
    provider, client = _provider(store, clock=clock)

    tokens = iter(["at-stale", "at-fresh"])
    respx.post(TOKEN_URL).mock(
        side_effect=lambda r: httpx.Response(
            200, json={"access_token": next(tokens), "expires_in": 3600}
        )
    )

    api = "https://api.example.com/resource"
    seen_auth: list[str] = []

    def _api_handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers["Authorization"]
        seen_auth.append(auth)
        if auth == "Bearer at-stale":
            return httpx.Response(401, headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
        return httpx.Response(200, json={"ok": True})

    respx.get(api).mock(side_effect=_api_handler)

    req = _refresh_req()
    resp = await provider.request(TENANT, req, "GET", api)

    assert resp.status_code == 200
    # Exactly one refresh-and-retry: stale token first, fresh token second.
    assert seen_auth == ["Bearer at-stale", "Bearer at-fresh"]
    await client.aclose()


@respx.mock
async def test_401_invalid_grant_raises_reauth_required() -> None:
    store = FakeSecretStore()
    await store.set_secret(TENANT, "client_secret", b"cs")
    await store.set_secret(TENANT, "refresh_token", b"rt")
    clock = FixedClock(datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC))
    provider, client = _provider(store, clock=clock)

    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
    )
    api = "https://api.example.com/resource"
    respx.get(api).mock(
        return_value=httpx.Response(
            401, headers={"WWW-Authenticate": 'Bearer error="invalid_grant"'}
        )
    )

    req = _refresh_req()
    with pytest.raises(ReauthRequired):
        await provider.request(TENANT, req, "GET", api)
    await client.aclose()


@respx.mock
async def test_429_honors_retry_after_with_backoff() -> None:
    store = FakeSecretStore()
    await store.set_secret(TENANT, "client_secret", b"cs")
    await store.set_secret(TENANT, "refresh_token", b"rt")
    clock = FixedClock(datetime(2026, 6, 26, 12, 0, 0, tzinfo=UTC))
    sleeper = RecordingSleeper()
    provider, client = _provider(store, clock=clock, sleeper=sleeper)

    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
    )

    api = "https://api.example.com/resource"
    responses = iter(
        [
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    respx.get(api).mock(side_effect=lambda r: next(responses))

    req = _refresh_req()
    resp = await provider.request(TENANT, req, "GET", api)

    assert resp.status_code == 200
    # The backoff sleeper was called once, honoring Retry-After of 7 seconds,
    # and no real time elapsed (injected sleeper).
    assert sleeper.delays == [7.0]
    await client.aclose()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validate_rejects_missing_secret_ref() -> None:
    from pansoph_connector_sdk.auth import AuthError

    req = AuthRequirement(
        grant=GrantType.REFRESH_TOKEN,
        token_url=TOKEN_URL,
        secret_refs={"client_secret": SecretRef(TENANT, "client_secret")},
        extra={"client_id": "cid"},
    )
    with pytest.raises(AuthError):
        req.validate()


def test_validate_rejects_missing_extra() -> None:
    from pansoph_connector_sdk.auth import AuthError

    req = AuthRequirement(
        grant=GrantType.JWT_BEARER,
        token_url=TOKEN_URL,
        secret_refs={"private_key": SecretRef(TENANT, "private_key")},
        extra={"iss": "x", "sub": "y"},  # missing aud
    )
    with pytest.raises(AuthError):
        req.validate()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _refresh_req() -> AuthRequirement:
    return AuthRequirement(
        grant=GrantType.REFRESH_TOKEN,
        token_url=TOKEN_URL,
        scopes=frozenset({"read"}),
        secret_refs={
            "client_secret": SecretRef(TENANT, "client_secret"),
            "refresh_token": SecretRef(TENANT, "refresh_token"),
        },
        extra={"client_id": "cid"},
    )
