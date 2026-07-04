"""Hermetic tests for the Atlassian OAuth 2.0 (3LO) provider.

No network: the token exchange is mocked with respx, and no client credentials
touch the environment (the provider methods take client_id / client_secret as
arguments). These prove the authorize URL is built with the required 3LO params
(audience, prompt=consent, response_type, the read-only scopes) and that the code
exchange POSTs a JSON body and parses the standard token response, including the
rotating refresh token that offline_access yields.

Verified against the Atlassian 3LO docs on 2026-07-04. No em dashes anywhere.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from pansoph_core.oauth.errors import CodeExchangeError
from pansoph_core.oauth.providers.atlassian import DEFAULT_SCOPES, PROVIDER

_TOKEN_URL = "https://auth.atlassian.com/oauth/token"


# ---------------------------------------------------------------------------
# authorize URL construction
# ---------------------------------------------------------------------------


def test_authorize_url_has_required_3lo_params() -> None:
    url = PROVIDER.build_authorize_url(
        client_id="cid",
        redirect_uri="https://pansoph.example.com/api/oauth/atlassian/callback",
        state="state-token",
    )
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    assert parsed.netloc == "auth.atlassian.com"
    assert parsed.path == "/authorize"
    # The 3LO flow REQUIRES audience=api.atlassian.com and prompt=consent.
    assert qs["audience"] == ["api.atlassian.com"]
    assert qs["prompt"] == ["consent"]
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == ["cid"]
    assert qs["state"] == ["state-token"]
    assert qs["redirect_uri"] == ["https://pansoph.example.com/api/oauth/atlassian/callback"]
    # No PKCE for the confidential-client 3LO flow.
    assert "code_challenge" not in qs
    assert PROVIDER.uses_pkce is False


def test_authorize_url_default_scopes_are_read_only_and_include_offline() -> None:
    url = PROVIDER.build_authorize_url(
        client_id="cid",
        redirect_uri="https://x/cb",
        state="s",
    )
    qs = parse_qs(urlparse(url).query)
    # Scopes are space-separated per RFC 6749; parse_qs keeps the single joined value.
    scopes = qs["scope"][0].split(" ")
    assert scopes == list(DEFAULT_SCOPES)
    # offline_access is what makes Atlassian return a refresh token.
    assert "offline_access" in scopes
    # Every non-offline scope is a read scope: no write / manage / delete verbs.
    for scope in scopes:
        if scope == "offline_access":
            continue
        assert scope.startswith("read:"), f"non-read scope leaked into defaults: {scope}"


def test_authorize_url_honors_connector_scope_override() -> None:
    url = PROVIDER.build_authorize_url(
        client_id="cid",
        redirect_uri="https://x/cb",
        state="s",
        scopes=("read:jira-work", "offline_access"),
    )
    qs = parse_qs(urlparse(url).query)
    assert qs["scope"] == ["read:jira-work offline_access"]


# ---------------------------------------------------------------------------
# code exchange
# ---------------------------------------------------------------------------


@respx.mock
async def test_exchange_code_posts_json_and_parses_tokens() -> None:
    route = respx.post(_TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "at-abc",
                "refresh_token": "rt-rotating-1",
                "expires_in": 3600,
                "scope": "read:jira-work offline_access",
                "token_type": "Bearer",
            },
        )
    )
    async with httpx.AsyncClient() as http:
        result = await PROVIDER.exchange_code(
            http=http,
            client_id="cid",
            client_secret="csecret",
            code="auth-code",
            redirect_uri="https://x/cb",
        )

    assert result.access_token == "at-abc"
    assert result.refresh_token == "rt-rotating-1"
    assert result.expires_in == 3600
    assert result.token_type == "Bearer"

    # Atlassian documents a JSON body, not form encoding. Assert we sent JSON.
    request = route.calls.last.request
    assert request.headers["content-type"].startswith("application/json")
    import json

    sent = json.loads(request.content)
    assert sent == {
        "grant_type": "authorization_code",
        "client_id": "cid",
        "client_secret": "csecret",
        "code": "auth-code",
        "redirect_uri": "https://x/cb",
    }


@respx.mock
async def test_refresh_rotates_the_refresh_token() -> None:
    route = respx.post(_TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "at-new",
                "refresh_token": "rt-rotating-2",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )
    )
    async with httpx.AsyncClient() as http:
        result = await PROVIDER.refresh(
            http=http,
            client_id="cid",
            client_secret="csecret",
            refresh_token="rt-rotating-1",
        )

    # The rotated refresh token must be carried through so the caller can persist it.
    assert result.refresh_token == "rt-rotating-2"
    assert result.access_token == "at-new"
    import json

    sent = json.loads(route.calls.last.request.content)
    assert sent["grant_type"] == "refresh_token"
    assert sent["refresh_token"] == "rt-rotating-1"


@respx.mock
async def test_exchange_code_surfaces_non_2xx() -> None:
    respx.post(_TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    async with httpx.AsyncClient() as http:
        with pytest.raises(CodeExchangeError):
            await PROVIDER.exchange_code(
                http=http,
                client_id="cid",
                client_secret="csecret",
                code="bad",
                redirect_uri="https://x/cb",
            )
