"""GitHub OAuth provider (user-to-server) plus the GitHub App installation path.

Docs verified 2026-07-04 against GitHub's current official docs (REST API version
2026-03-10):
  authorize/token endpoints and PKCE:
    https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps
    authorize: https://github.com/login/oauth/authorize
    token:     https://github.com/login/oauth/access_token
  scopes for OAuth apps (read-only mapping):
    https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps
  GitHub App user access tokens (refresh only when expiring tokens are enabled):
    https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app
  GitHub App JWT (RS256, iss=app/client id, iat backdated 60s, exp <= 10 min):
    https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app
  Installation access token endpoint (JWT auth, token lasts ~1h):
    https://docs.github.com/en/rest/apps/apps?apiVersion=2022-11-28

Corrections this module makes to the earlier stub, both verified above:
  - Scope separator is a SPACE in the authorize request (RFC 6749). GitHub only
    uses a comma in the token RESPONSE scope field, which does not touch authorize
    URL construction, so scope_separator stays the default space (not a comma).
  - GitHub's OAuth now supports and strongly recommends S256 PKCE, so uses_pkce is
    True. The base flow generates the verifier and the base exchange sends it.

Scope note: GitHub has no read-only scope for PRIVATE repository contents, so a
connector that must read private files needs the `repo` scope; `read:org` and
`read:user` are read-only and back the collaborator/team ACL sync. A connector can
narrow this per instance via ConfigField.oauth_scopes (for example public_repo for
public-only). The base exchange sends Accept: application/json so GitHub returns the
standard JSON body, and the base refresh already POSTs grant_type=refresh_token,
which is exactly the GitHub App expiring-token refresh grant.

The GitHub App installation flow below is DISTINCT from user OAuth: it signs an app
JWT with the app's private key (.pem) and exchanges it at
POST /app/installations/{installation_id}/access_tokens for a ~1h installation
token. It uses no client_id/secret and issues no refresh token; the token is minted
fresh from the .pem each cycle. The connect UX collects app_id/installation_id and
the stored .pem secret, and the connector reads the resulting token from the same
sealed config key the manual-paste and user-OAuth paths use.

No em dashes anywhere.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import jwt

from ..errors import CodeExchangeError
from .base import OAuthProvider, TokenResult

if TYPE_CHECKING:
    import httpx

# GitHub's current REST API version, matched to the github connector module.
API_VERSION = "2026-03-10"

# A GitHub App JWT may live at most 10 minutes; we backdate iat 60s for clock drift
# and set a conservative lifetime well under the ceiling.
_JWT_IAT_BACKDATE_SECONDS = 60
_JWT_LIFETIME_SECONDS = 540  # 9 minutes, under GitHub's 10 minute cap

PROVIDER = OAuthProvider(
    name="github",
    authorize_url="https://github.com/login/oauth/authorize",
    token_url="https://github.com/login/oauth/access_token",  # noqa: S106  public endpoint
    # Read-oriented defaults: `repo` is the only scope that reads PRIVATE repo
    # contents (GitHub has no read-only private-repo scope); read:org and read:user
    # are read-only and back the collaborator/team ACL sync. Narrow per instance
    # (for example public_repo) via ConfigField.oauth_scopes.
    default_scopes=("repo", "read:org", "read:user"),
    # GitHub now supports and strongly recommends S256 PKCE for the authorize code
    # flow; the base flow generates the verifier and challenge when this is True.
    uses_pkce=True,
    # RFC 6749 space separator for the authorize request. GitHub's comma quirk is
    # only in the token RESPONSE scope field, which does not affect this URL.
    scope_separator=" ",
    env_prefix="GITHUB",
)


def build_app_jwt(*, app_id: str, private_key_pem: str, now: int | None = None) -> str:
    """Sign a short-lived GitHub App JWT (RS256) for the installation-token exchange.

    Claims per the current GitHub App docs: `iss` is the app id (numeric) or the
    app's client id, `iat` is backdated 60 seconds against clock drift, and `exp` is
    kept under GitHub's 10 minute ceiling. `now` is injectable so a test can pin the
    clock; production passes None and the current time is used.

    Raises CodeExchangeError with an actionable message when the .pem is not a valid
    RSA private key, so a misconfigured secret fails legibly rather than emitting a
    malformed token that GitHub rejects opaquely.
    """
    issued_at = int(time.time()) if now is None else now
    payload = {
        "iat": issued_at - _JWT_IAT_BACKDATE_SECONDS,
        "exp": issued_at + _JWT_LIFETIME_SECONDS,
        "iss": app_id,
    }
    try:
        return jwt.encode(payload, private_key_pem, algorithm="RS256")
    except (ValueError, TypeError, jwt.PyJWTError) as exc:
        raise CodeExchangeError(
            "github app JWT signing failed; the app private key (.pem) is missing "
            f"or not a valid RSA key: {exc}"
        ) from exc


async def mint_installation_token(
    *,
    http: httpx.AsyncClient,
    app_id: str,
    installation_id: str,
    private_key_pem: str,
    base_url: str = "https://api.github.com",
    now: int | None = None,
) -> TokenResult:
    """Mint a ~1h GitHub App installation access token (the App path, not user OAuth).

    Signs an app JWT from the .pem, then POSTs to
    /app/installations/{installation_id}/access_tokens with the JWT as a bearer
    token. GitHub returns `{"token": ..., "expires_at": ...}`; that token is the
    credential the github connector uses, so it is returned as the TokenResult
    access_token with no refresh token (the token is re-minted from the .pem each
    cycle). `base_url` is overridable for GitHub Enterprise; `now` is injectable so a
    test can pin the JWT clock.

    Raises CodeExchangeError on a non-2xx response or a body without a token, the
    same fail-fast contract the user-OAuth exchange follows, so a failure is
    surfaced and never seals an empty credential.
    """
    app_jwt = build_app_jwt(app_id=app_id, private_key_pem=private_key_pem, now=now)
    url = f"{base_url.rstrip('/')}/app/installations/{installation_id}/access_tokens"
    response = await http.post(
        url,
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        },
    )
    if response.status_code >= 400:
        raise CodeExchangeError(
            "github installation-token endpoint returned "
            f"HTTP {response.status_code}: {response.text[:300]}"
        )
    return _installation_token_from_body(response)


def _installation_token_from_body(response: httpx.Response) -> TokenResult:
    """Parse GitHub's installation-token body `{"token": ..., "expires_at": ...}`.

    Installation tokens do not carry a refresh token or an expires_in; the body
    gives an absolute `expires_at` timestamp, which is converted to a remaining
    lifetime in seconds so the sealed shape matches the user-OAuth TokenResult.
    """
    try:
        body = response.json()
    except ValueError as exc:
        raise CodeExchangeError("github installation-token body was not JSON") from exc
    if not isinstance(body, dict):
        raise CodeExchangeError("github installation-token body was not an object")
    token = body.get("token")
    if not isinstance(token, str) or not token:
        raise CodeExchangeError("github installation-token body had no token")
    expires_in = _seconds_until(body.get("expires_at"))
    return TokenResult(access_token=token, expires_in=expires_in)


def _seconds_until(expires_at: object) -> int | None:
    """Remaining lifetime in seconds from a GitHub ISO 8601 `expires_at`, or None.

    GitHub returns an absolute UTC timestamp like 2026-07-04T12:00:00Z; the sealed
    TokenResult wants a relative lifetime, so convert here and clamp negatives to 0.
    A missing or unparseable value yields None (lifetime unknown), never an error,
    since the token itself is already validated by the caller.
    """
    if not isinstance(expires_at, str) or not expires_at:
        return None
    from datetime import UTC, datetime

    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    remaining = int((parsed - datetime.now(UTC)).total_seconds())
    return max(remaining, 0)
