"""Google OAuth 2.0 provider (authorization-code flow with offline access).

Implemented against Google's current official OAuth 2.0 docs. Verified 2026-07-04:

  Web server flow:  https://developers.google.com/identity/protocols/oauth2/web-server
  OpenID Connect:   https://developers.google.com/identity/openid-connect/openid-connect
  API scopes list:  https://developers.google.com/identity/protocols/oauth2/scopes
  Drive scopes:     https://developers.google.com/workspace/drive/api/guides/api-specific-auth
  authorize: https://accounts.google.com/o/oauth2/v2/auth
  token:     https://oauth2.googleapis.com/token
  API model: OAuth 2.0 (authorization code grant, RFC 6749) with PKCE (RFC 7636).

Verified behavior that shapes this module:
  - Endpoints are HTTPS only; plain HTTP is refused. The two URLs above are the
    current documented endpoints for web server apps.
  - A refresh token is returned only when access_type=offline is on the authorize
    request, and reliably on every consent only when prompt=consent forces the
    consent screen. Both are set in extra_authorize_params below. Without them a
    silent re-auth can return an access token with no refresh token, which breaks
    the sync-time refresh path, so both are required, not optional.
  - PKCE with S256 is accepted by Google's authorize endpoint, so uses_pkce=True
    for defense in depth. Caveat: for the Web Application client type Google still
    requires the client_secret at the token exchange (PKCE does not make the client
    public). The base exchange already sends client_secret, so PKCE here is additive
    and correct; do not drop the secret.
  - The token response is standard RFC 6749 JSON (access_token, refresh_token,
    expires_in, scope, token_type, plus an id_token). The base parse_token_response
    handles it; no override is needed. Connectors that need the user email decode
    the id_token or call the userinfo endpoint themselves; that is not this module.
  - Refresh uses grant_type=refresh_token and returns a new access_token with no new
    refresh_token (Google keeps the original), which the base refresh handles.

Scope policy: default_scopes here is the minimal identity set (openid email profile)
so an authorize with no connector override still succeeds and yields a usable id.
Each Google connector declares the data scope it actually needs via its OAUTH
ConfigField.oauth_scopes (Drive: drive.readonly; Gmail: gmail.readonly), which the
flow passes through to build_authorize_url in place of these defaults. Widening the
provider default to a data scope is deliberately avoided so the shared registered
app never requests more than a given connector needs.

Client credentials come from PANSOPH_OAUTH_GOOGLE_CLIENT_ID / _SECRET (or the secret
store under _oauth/google/client_id and _oauth/google/client_secret).

No em dashes anywhere.
"""

from __future__ import annotations

from typing import Final

from .base import OAuthProvider

# The current documented Google OAuth 2.0 endpoints for web server apps.
AUTHORIZE_URL: Final = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL: Final = "https://oauth2.googleapis.com/token"  # noqa: S105  public endpoint, not a secret

# Minimal OpenID Connect identity scopes. Data scopes are added per connector via
# ConfigField.oauth_scopes, never baked in here (least privilege for the shared app).
DEFAULT_SCOPES: Final[tuple[str, ...]] = ("openid", "email", "profile")

# The verified read-only data scopes, exported so the Google connectors declare them
# by reference rather than re-typing the exact strings (a wrong scope silently fails
# or oversharing, so keep the canonical value in one place).
DRIVE_READONLY_SCOPE: Final = "https://www.googleapis.com/auth/drive.readonly"
GMAIL_READONLY_SCOPE: Final = "https://www.googleapis.com/auth/gmail.readonly"

# access_type=offline makes Google issue a refresh token; prompt=consent forces the
# consent screen so a refresh token is returned on every connect, not just the first.
_OFFLINE_PARAMS: Final[dict[str, str]] = {"access_type": "offline", "prompt": "consent"}

PROVIDER: Final = OAuthProvider(
    name="google",
    authorize_url=AUTHORIZE_URL,
    token_url=TOKEN_URL,
    default_scopes=DEFAULT_SCOPES,
    uses_pkce=True,
    extra_authorize_params=_OFFLINE_PARAMS,
    env_prefix="GOOGLE",
)
