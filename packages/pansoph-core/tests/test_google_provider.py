"""Hermetic tests for the Google OAuth 2.0 provider.

No network, no Postgres. The token exchange is mocked with respx. These prove the
two things the provider owns: the authorize URL is built with the offline access and
PKCE parameters Google needs to return a refresh token, and a standard RFC 6749 code
exchange is parsed into a TokenResult (access token plus refresh token). No em dashes
anywhere.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import respx
from pansoph_core.oauth.providers.base import new_pkce_verifier, pkce_challenge_s256
from pansoph_core.oauth.providers.google import (
    DRIVE_READONLY_SCOPE,
    GMAIL_READONLY_SCOPE,
    PROVIDER,
)


def test_provider_targets_current_google_endpoints() -> None:
    # The exact endpoints verified against Google Identity docs on 2026-07-04.
    assert PROVIDER.name == "google"
    assert PROVIDER.authorize_url == "https://accounts.google.com/o/oauth2/v2/auth"
    assert PROVIDER.token_url == "https://oauth2.googleapis.com/token"
    assert PROVIDER.uses_pkce is True
    # Offline access plus a forced consent are what make Google return a refresh token.
    assert PROVIDER.extra_authorize_params["access_type"] == "offline"
    assert PROVIDER.extra_authorize_params["prompt"] == "consent"
    # Default scopes are the minimal identity set; data scopes come from connectors.
    assert PROVIDER.default_scopes == ("openid", "email", "profile")


def test_authorize_url_carries_offline_pkce_and_connector_scope() -> None:
    verifier = new_pkce_verifier()
    challenge = pkce_challenge_s256(verifier)
    url = PROVIDER.build_authorize_url(
        client_id="gid",
        redirect_uri="https://pansoph.example.com/api/oauth/google/callback",
        state="state-123",
        scopes=(DRIVE_READONLY_SCOPE,),
        code_challenge=challenge,
    )
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.google.com"
    assert parsed.path == "/o/oauth2/v2/auth"
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == ["gid"]
    assert qs["redirect_uri"] == ["https://pansoph.example.com/api/oauth/google/callback"]
    assert qs["state"] == ["state-123"]
    # The connector-declared scope overrides the provider defaults.
    assert qs["scope"] == [DRIVE_READONLY_SCOPE]
    # PKCE S256 is present, and offline access plus consent are always merged in.
    assert qs["code_challenge"] == [challenge]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["access_type"] == ["offline"]
    assert qs["prompt"] == ["consent"]


def test_authorize_url_falls_back_to_identity_scopes() -> None:
    # With no connector scope override, the minimal identity scopes are requested.
    url = PROVIDER.build_authorize_url(
        client_id="gid",
        redirect_uri="https://pansoph.example.com/api/oauth/google/callback",
        state="s",
    )
    qs = parse_qs(urlparse(url).query)
    assert qs["scope"] == ["openid email profile"]


@respx.mock
async def test_exchange_code_parses_refresh_token() -> None:
    route = respx.post(PROVIDER.token_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ya29.at-token",
                "expires_in": 3599,
                "refresh_token": "1//rt-token",
                "scope": DRIVE_READONLY_SCOPE,
                "token_type": "Bearer",
                "id_token": "header.payload.sig",
            },
        )
    )
    async with httpx.AsyncClient() as http:
        result = await PROVIDER.exchange_code(
            http=http,
            client_id="gid",
            client_secret="gsecret",
            code="auth-code",
            redirect_uri="https://pansoph.example.com/api/oauth/google/callback",
            code_verifier="the-verifier",
        )

    # The offline exchange yields both tokens; the base parser reads the standard body.
    assert result.access_token == "ya29.at-token"
    assert result.refresh_token == "1//rt-token"
    assert result.expires_in == 3599
    assert result.scope == DRIVE_READONLY_SCOPE
    assert result.token_type == "Bearer"

    # The request body is the RFC 6749 authorization-code form, including the PKCE
    # verifier and the client secret Google still requires for a Web Application client.
    assert route.called
    sent = dict(parse_qs(route.calls.last.request.content.decode()))
    assert sent["grant_type"] == ["authorization_code"]
    assert sent["code"] == ["auth-code"]
    assert sent["code_verifier"] == ["the-verifier"]
    assert sent["client_id"] == ["gid"]
    assert sent["client_secret"] == ["gsecret"]
    assert sent["redirect_uri"] == ["https://pansoph.example.com/api/oauth/google/callback"]


@respx.mock
async def test_refresh_grant_mints_a_new_access_token() -> None:
    # Google's refresh response omits a new refresh_token (the original is kept).
    route = respx.post(PROVIDER.token_url).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ya29.new-at",
                "expires_in": 3599,
                "scope": GMAIL_READONLY_SCOPE,
                "token_type": "Bearer",
            },
        )
    )
    async with httpx.AsyncClient() as http:
        result = await PROVIDER.refresh(
            http=http,
            client_id="gid",
            client_secret="gsecret",
            refresh_token="1//rt-token",
        )

    assert result.access_token == "ya29.new-at"
    assert result.refresh_token is None
    sent = dict(parse_qs(route.calls.last.request.content.decode()))
    assert sent["grant_type"] == ["refresh_token"]
    assert sent["refresh_token"] == ["1//rt-token"]
