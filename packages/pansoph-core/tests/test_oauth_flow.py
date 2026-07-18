"""Hermetic tests for the OAuth connect subsystem: state round-trip and callback
sealing. No network, no Postgres. The token exchange is mocked with respx, the
connector manager and secret store are in-memory fakes, and the provider client
credentials are set in the environment. No em dashes anywhere.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from pansoph_core.oauth import InMemoryStateStore, OAuthFlow, get_provider, provider_names
from pansoph_core.oauth.errors import ProviderNotConfiguredError, StateError
from pansoph_core.oauth.providers.base import new_pkce_verifier, pkce_challenge_s256
from pansoph_core.oauth.state import OAuthState

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeSecretStore:
    """An in-memory SecretStore matching the async port used by seal_config."""

    def __init__(self) -> None:
        self._d: dict[tuple[str, str], bytes] = {}

    async def set_secret(self, tenant_id: str, name: str, value: bytes) -> None:
        self._d[(tenant_id, name)] = value

    async def get_secret(self, tenant_id: str, name: str) -> bytes | None:
        return self._d.get((tenant_id, name))

    async def delete_secret(self, tenant_id: str, name: str) -> None:
        self._d.pop((tenant_id, name), None)


class _FakeManager:
    """A stand-in ConnectorManager that records the created instance in memory and
    reports no connector spec (so the flow falls back to the default oauth key)."""

    def __init__(self) -> None:
        self.created: dict[str, Any] | None = None

    async def get_instance(self, instance_id: str) -> dict[str, Any] | None:
        return self.created

    async def create_instance(self, **kwargs: Any) -> dict[str, Any]:
        self.created = dict(kwargs)
        return self.created


def _flow(secret_store: _FakeSecretStore, http: httpx.AsyncClient) -> OAuthFlow:
    return OAuthFlow(
        manager=_FakeManager(),  # type: ignore[arg-type]
        state_store=InMemoryStateStore(),
        secret_store=secret_store,  # type: ignore[arg-type]
        http=http,
        public_base_url="https://pansoph.example.com",
    )


# ---------------------------------------------------------------------------
# State round-trip
# ---------------------------------------------------------------------------


async def test_state_store_round_trips_and_is_single_use() -> None:
    store = InMemoryStateStore()
    record = OAuthState(
        provider="google",
        connector="gdrive",
        instance="inst-1",
        tenant="t1",
        redirect_uri="https://pansoph.example.com/api/oauth/google/callback",
        code_verifier="v",
    )
    token = await store.put(record)

    got = await store.consume(token)
    assert got == record
    # Single use: a second consume of the same token returns nothing.
    assert await store.consume(token) is None


async def test_state_store_rejects_expired() -> None:
    store = InMemoryStateStore()
    record = OAuthState(
        provider="github",
        connector="gh",
        instance="i",
        tenant="t",
        redirect_uri="https://x/callback",
    )
    token = await store.put(record, ttl_seconds=0)
    # Expiry is immediate at ttl 0, so the record is already stale on consume.
    assert await store.consume(token) is None


async def test_state_store_unknown_token_is_none() -> None:
    store = InMemoryStateStore()
    assert await store.consume("never-issued") is None


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------


def test_pkce_challenge_is_deterministic_and_unpadded() -> None:
    verifier = new_pkce_verifier()
    challenge = pkce_challenge_s256(verifier)
    # S256 base64url output carries no padding and is stable for a given verifier.
    assert "=" not in challenge
    assert pkce_challenge_s256(verifier) == challenge


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_exposes_the_four_providers() -> None:
    assert provider_names() == ["atlassian", "github", "google", "slack"]


# ---------------------------------------------------------------------------
# authorize
# ---------------------------------------------------------------------------


async def test_authorize_builds_consent_url_with_state_and_pkce(monkeypatch: Any) -> None:
    monkeypatch.setenv("PANSOPH_OAUTH_GOOGLE_CLIENT_ID", "gid")
    async with httpx.AsyncClient() as http:
        flow = _flow(_FakeSecretStore(), http)
        url = await flow.authorize(
            provider="google", connector="gdrive", instance="inst-1", tenant="t1"
        )
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert parsed.netloc == "accounts.google.com"
    assert qs["client_id"] == ["gid"]
    assert qs["redirect_uri"] == ["https://pansoph.example.com/api/oauth/google/callback"]
    assert qs["state"]  # a real state token was recorded
    # Google is a PKCE provider, so the challenge and method are present.
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["access_type"] == ["offline"]


async def test_authorize_requests_only_the_connectors_scopes_not_the_provider_union(
    monkeypatch: Any,
) -> None:
    # Atlassian serves both Confluence and Jira, whose scope sets differ. Connecting the
    # Confluence connector must request only Confluence scopes, never the provider's
    # Jira+Confluence union, or Atlassian rejects the consent for a Jira scope the app may
    # not have (read:project-role:jira).
    monkeypatch.setenv("PANSOPH_OAUTH_ATLASSIAN_CLIENT_ID", "aid")
    async with httpx.AsyncClient() as http:
        flow = _flow(_FakeSecretStore(), http)
        url = await flow.authorize(
            provider="atlassian", connector="confluence", instance="c1", tenant="t1"
        )
    scope_param = parse_qs(urlparse(url).query).get("scope", [""])[0]
    scopes = set(scope_param.split())
    assert "read:confluence-content.all" in scopes, "confluence scopes must be requested"
    assert "read:project-role:jira" not in scopes, (
        "a Jira scope must NOT leak into a Confluence connect"
    )
    assert "read:jira-work" not in scopes


async def test_authorize_unconfigured_provider_raises(monkeypatch: Any) -> None:
    monkeypatch.delenv("PANSOPH_OAUTH_SLACK_CLIENT_ID", raising=False)
    async with httpx.AsyncClient() as http:
        flow = _flow(_FakeSecretStore(), http)
        with pytest.raises(ProviderNotConfiguredError):
            await flow.authorize(provider="slack", connector="slack", instance="i", tenant="t")


# ---------------------------------------------------------------------------
# handle_callback: exchange + seal
# ---------------------------------------------------------------------------


@respx.mock
async def test_callback_exchanges_and_seals_tokens(monkeypatch: Any) -> None:
    monkeypatch.setenv("PANSOPH_OAUTH_GOOGLE_CLIENT_ID", "gid")
    monkeypatch.setenv("PANSOPH_OAUTH_GOOGLE_CLIENT_SECRET", "gsecret")

    token_url = get_provider("google").token_url
    respx.post(token_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "at-123",
                "refresh_token": "rt-456",
                "expires_in": 3600,
                "scope": "email profile",
                "token_type": "Bearer",
            },
        )
    )

    secret_store = _FakeSecretStore()
    async with httpx.AsyncClient() as http:
        flow = _flow(secret_store, http)
        # Seed a live state as if authorize had run.
        state_token = await flow.state_store.put(
            OAuthState(
                provider="google",
                connector="gdrive",
                instance="inst-1",
                tenant="t1",
                redirect_uri="https://pansoph.example.com/api/oauth/google/callback",
                code_verifier="verifier",
            )
        )
        instance_id = await flow.handle_callback(
            provider="google", code="auth-code", state=state_token
        )

    assert instance_id == "inst-1"
    manager: _FakeManager = flow.manager  # type: ignore[assignment]
    assert manager.created is not None
    config = manager.created["config"]
    # The tokens are sealed: the config carries handles, never the plaintext token.
    assert config["oauth_token"] == {"_secret": "inst-1/oauth_token"}
    assert config["oauth_token__refresh_token"] == {"_secret": "inst-1/oauth_token__refresh_token"}
    # The plaintext access and refresh tokens live only in the secret store.
    assert await secret_store.get_secret("t1", "inst-1/oauth_token") == b"at-123"
    assert await secret_store.get_secret("t1", "inst-1/oauth_token__refresh_token") == b"rt-456"
    # The provider client_secret is mirrored under the reserved _oauth tenant so the
    # sync-time refresh minter can resolve it.
    assert await secret_store.get_secret("_oauth", "google/client_secret") == b"gsecret"


@respx.mock
async def test_callback_rejects_forged_state(monkeypatch: Any) -> None:
    monkeypatch.setenv("PANSOPH_OAUTH_GOOGLE_CLIENT_ID", "gid")
    monkeypatch.setenv("PANSOPH_OAUTH_GOOGLE_CLIENT_SECRET", "gsecret")
    async with httpx.AsyncClient() as http:
        flow = _flow(_FakeSecretStore(), http)
        with pytest.raises(StateError):
            await flow.handle_callback(provider="google", code="c", state="not-a-real-state")


@respx.mock
async def test_callback_rejects_provider_mismatch(monkeypatch: Any) -> None:
    monkeypatch.setenv("PANSOPH_OAUTH_GOOGLE_CLIENT_ID", "gid")
    monkeypatch.setenv("PANSOPH_OAUTH_GOOGLE_CLIENT_SECRET", "gsecret")
    async with httpx.AsyncClient() as http:
        flow = _flow(_FakeSecretStore(), http)
        # A state minted for slack cannot be replayed at google's callback.
        state_token = await flow.state_store.put(
            OAuthState(
                provider="slack",
                connector="slack",
                instance="i",
                tenant="t",
                redirect_uri="https://x/callback",
            )
        )
        with pytest.raises(StateError):
            await flow.handle_callback(provider="google", code="c", state=state_token)
