"""Hermetic tests for the GitHub OAuth provider and the GitHub App path.

No network: the user-OAuth token exchange and the App installation-token endpoint
are both mocked with respx. A throwaway RSA key is generated in-test so the app JWT
signs and verifies without any committed secret. These assertions pin the doc-driven
facts the provider encodes (space-delimited scopes, S256 PKCE, the installation-token
shape), so a future edit that regresses them fails loudly. No em dashes anywhere.
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pansoph_core.oauth.errors import CodeExchangeError
from pansoph_core.oauth.providers import github
from pansoph_core.oauth.providers.base import pkce_challenge_s256


def _rsa_pem() -> tuple[str, str]:
    """Generate a throwaway RSA keypair, returning (private_pem, public_pem)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return private_pem, public_pem


# ---------------------------------------------------------------------------
# authorize URL (user OAuth)
# ---------------------------------------------------------------------------


def test_authorize_url_uses_space_scopes_and_s256_pkce() -> None:
    verifier = "a-fixed-verifier-string-of-sufficient-length-000000"
    url = github.PROVIDER.build_authorize_url(
        client_id="cid",
        redirect_uri="https://pansoph.example.com/api/oauth/github/callback",
        state="state-token",
        code_challenge=pkce_challenge_s256(verifier),
    )
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert parsed.netloc == "github.com"
    assert parsed.path == "/login/oauth/authorize"
    assert qs["client_id"] == ["cid"]
    assert qs["state"] == ["state-token"]
    assert qs["response_type"] == ["code"]
    # GitHub takes SPACE-delimited scopes in the authorize request (RFC 6749). parse_qs
    # decodes the "+"/%20 back to a single space-joined string.
    assert qs["scope"] == ["repo read:org read:user"]
    # GitHub now supports S256 PKCE, so the challenge and method must be present.
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["code_challenge"] == [pkce_challenge_s256(verifier)]


def test_provider_declares_pkce_and_space_separator() -> None:
    assert github.PROVIDER.uses_pkce is True
    assert github.PROVIDER.scope_separator == " "
    assert github.PROVIDER.default_scopes == ("repo", "read:org", "read:user")


# ---------------------------------------------------------------------------
# code exchange (user OAuth)
# ---------------------------------------------------------------------------


@respx.mock
async def test_exchange_code_parses_standard_json_body() -> None:
    route = respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "gho_abc123",
                "token_type": "bearer",
                "scope": "repo,read:org",
            },
        )
    )
    async with httpx.AsyncClient() as http:
        result = await github.PROVIDER.exchange_code(
            http=http,
            client_id="cid",
            client_secret="csecret",
            code="the-code",
            redirect_uri="https://pansoph.example.com/api/oauth/github/callback",
            code_verifier="the-verifier",
        )
    assert result.access_token == "gho_abc123"
    # A non-expiring OAuth app token carries no refresh token.
    assert result.refresh_token is None
    # The PKCE verifier and the authorization-code grant were sent in the form body.
    sent = route.calls.last.request
    body = sent.content.decode("ascii")
    assert "grant_type=authorization_code" in body
    assert "code_verifier=the-verifier" in body
    # Accept: application/json is what makes GitHub return the JSON body we parsed.
    assert sent.headers["Accept"] == "application/json"


@respx.mock
async def test_exchange_code_error_body_raises() -> None:
    respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=httpx.Response(
            200, json={"error": "bad_verification_code", "error_description": "expired"}
        )
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(CodeExchangeError):
            await github.PROVIDER.exchange_code(
                http=http,
                client_id="cid",
                client_secret="csecret",
                code="stale",
                redirect_uri="https://x/callback",
            )


# ---------------------------------------------------------------------------
# GitHub App installation path (distinct from user OAuth)
# ---------------------------------------------------------------------------


def test_build_app_jwt_signs_verifiable_rs256_claims() -> None:
    private_pem, public_pem = _rsa_pem()
    now = int(time.time())
    token = github.build_app_jwt(app_id="123456", private_key_pem=private_pem, now=now)
    decoded = jwt.decode(token, public_pem, algorithms=["RS256"])
    assert decoded["iss"] == "123456"
    # iat is backdated 60s for clock drift, and exp stays under GitHub's 10 minute cap.
    assert decoded["iat"] == now - 60
    assert 0 < decoded["exp"] - now <= 600


def test_build_app_jwt_bad_key_raises() -> None:
    with pytest.raises(CodeExchangeError):
        github.build_app_jwt(app_id="1", private_key_pem="not-a-pem")


@respx.mock
async def test_mint_installation_token_returns_token_and_lifetime() -> None:
    private_pem, _ = _rsa_pem()
    expires_at = "2999-01-01T00:00:00Z"  # far future so the remaining lifetime is positive
    route = respx.post("https://api.github.com/app/installations/42/access_tokens").mock(
        return_value=httpx.Response(
            201, json={"token": "ghs_installation_token", "expires_at": expires_at}
        )
    )
    async with httpx.AsyncClient() as http:
        result = await github.mint_installation_token(
            http=http,
            app_id="123456",
            installation_id="42",
            private_key_pem=private_pem,
        )
    assert result.access_token == "ghs_installation_token"
    assert result.refresh_token is None
    assert result.expires_in is not None and result.expires_in > 0
    # The signed app JWT is presented as a bearer token to the installation endpoint.
    auth = route.calls.last.request.headers["Authorization"]
    assert auth.startswith("Bearer ")
    assert route.calls.last.request.headers["X-GitHub-Api-Version"] == github.API_VERSION


@respx.mock
async def test_mint_installation_token_http_error_raises() -> None:
    private_pem, _ = _rsa_pem()
    respx.post("https://api.github.com/app/installations/42/access_tokens").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(CodeExchangeError):
            await github.mint_installation_token(
                http=http,
                app_id="123456",
                installation_id="42",
                private_key_pem=private_pem,
            )
