"""Slack OAuth v2 provider.

Docs verified 2026-07-04 against the current official Slack docs:
  Installing with OAuth (v2 flow):
    https://docs.slack.dev/authentication/installing-with-oauth
  oauth.v2.access method reference (initial exchange and refresh):
    https://api.slack.com/methods/oauth.v2.access
  Token rotation (refresh_token grant, expires_in, xoxe- refresh tokens):
    https://docs.slack.dev/authentication/using-token-rotation
  PKCE (public clients only, not the confidential web-app flow):
    https://docs.slack.dev/authentication/using-pkce
  Scopes reference (bot token read scopes):
    https://docs.slack.dev/reference/scopes

API endpoints (Slack Web API is versionless; these are stable):
  authorize: https://slack.com/oauth/v2/authorize
  token:     https://slack.com/api/oauth.v2.access

Provider-specific behavior this module encodes:

  - Scope separator is a COMMA, not a space. Slack's authorize URL takes bot
    scopes as a comma-separated `scope` list (like GitHub), so scope_separator
    is ",". User scopes go in a separate `user_scope` param; pansoph's connector
    only needs a bot token, so this provider never requests user scopes.

  - The initial code exchange does NOT send grant_type. RFC 6749 uses
    grant_type=authorization_code, but Slack's oauth.v2.access infers the initial
    exchange from the presence of `code` and reserves grant_type for the refresh
    call (grant_type=refresh_token). The base exchange_code sends
    grant_type=authorization_code, so exchange_code is overridden here to omit it
    and send exactly the params Slack documents (client_id, client_secret, code,
    redirect_uri). PKCE code_verifier is passed through for the public-client case.

  - The token response is an envelope, not the plain RFC 6749 shape:
    {"ok": true, "access_token": "xoxb-...", "token_type": "bot", "scope": "...",
     "team": {"id": ...}, "authed_user": {...}, ...} and Slack returns HTTP 200
    even on failure with {"ok": false, "error": ...}. parse_token_response below
    inspects `ok` before trusting the body, and carries the team id through in
    TokenResult.extra so a connector can address the right workspace.

  - Token rotation is opt-in per app. When enabled, the response also carries
    "expires_in": 43200 (12 hours) and "refresh_token": "xoxe-...". Refreshing
    calls the same endpoint with grant_type=refresh_token; the base refresh()
    already sends exactly that and routes the response through this parser, so the
    rotating-token path is handled without an override. Refresh tokens are
    single-use and rotate, so the caller must persist the new refresh_token on
    each refresh (the shared CredentialProvider does this for REFRESH_TOKEN grants).

  - Client credentials come from PANSOPH_OAUTH_SLACK_CLIENT_ID / _SECRET (or the
    secret store under _oauth/slack/*), resolved by the base.

Default scopes are read-only bot scopes: channels:read and groups:read to list
public and private channels, channels:history and groups:history to read
messages, and users:read for the membership ACL lookup. These match exactly what
the Slack connector needs and nothing more.

No em dashes anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..errors import CodeExchangeError
from .base import OAuthProvider, TokenResult, token_result_from_standard_body

# Read-only bot token scopes the Slack connector needs. channels:* cover public
# channels, groups:* cover private channels, users:read backs the membership ACL.
SLACK_READ_SCOPES: tuple[str, ...] = (
    "channels:read",
    "channels:history",
    "groups:read",
    "groups:history",
    "users:read",
)


@dataclass(frozen=True)
class _SlackProvider(OAuthProvider):
    """Slack override for the two ways Slack departs from RFC 6749.

    1. The initial code exchange omits grant_type (Slack infers it from `code`).
    2. The token body is an envelope returned with HTTP 200 even on failure, so
       success is decided by the `ok` field, not the status code.
    """

    async def exchange_code(
        self,
        *,
        http: httpx.AsyncClient,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
        code_verifier: str | None = None,
    ) -> TokenResult:
        """Exchange an authorization code at oauth.v2.access, Slack's way.

        Sends only the params Slack documents (no grant_type on the initial
        exchange). The PKCE code_verifier is forwarded when one was used (public
        clients). Response parsing goes through parse_token_response so the
        envelope and the ok flag are honored.
        """
        form: dict[str, str] = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        if code_verifier:
            form["code_verifier"] = code_verifier
        response = await http.post(
            self.token_url, data=form, headers={"Accept": "application/json"}
        )
        if response.status_code >= 400:
            raise CodeExchangeError(
                f"provider {self.name!r} token endpoint returned "
                f"HTTP {response.status_code}: {response.text[:300]}"
            )
        return self.parse_token_response(response)

    def parse_token_response(self, response: httpx.Response) -> TokenResult:
        """Unwrap the oauth.v2.access envelope into a TokenResult.

        Slack returns HTTP 200 with `{"ok": false, "error": ...}` on failure, so
        the status-code check in the base is not enough; this inspects `ok` first.
        On success the top-level access_token is the bot token (token_type "bot"),
        which the standard-body helper reads directly. When token rotation is
        enabled the body also carries expires_in and a refresh_token, both picked
        up by the helper. The team id is carried through in `extra` so a connector
        can pin the workspace without a second API call.
        """
        try:
            body = response.json()
        except ValueError as exc:
            raise CodeExchangeError("slack returned a non-JSON token body") from exc
        if not isinstance(body, dict) or not body.get("ok", False):
            error = body.get("error") if isinstance(body, dict) else "malformed response"
            raise CodeExchangeError(f"slack oauth.v2.access failed: {error}")
        result = token_result_from_standard_body("slack", body)
        team = body.get("team")
        team_id = team.get("id") if isinstance(team, dict) else None
        if isinstance(team_id, str) and team_id:
            result = TokenResult(
                access_token=result.access_token,
                refresh_token=result.refresh_token,
                expires_in=result.expires_in,
                scope=result.scope,
                token_type=result.token_type,
                extra={"team_id": team_id},
            )
        return result


PROVIDER = _SlackProvider(
    name="slack",
    authorize_url="https://slack.com/oauth/v2/authorize",
    token_url="https://slack.com/api/oauth.v2.access",  # noqa: S106  public endpoint
    default_scopes=SLACK_READ_SCOPES,
    uses_pkce=False,  # confidential web-app flow uses client_secret, not PKCE
    scope_separator=",",  # Slack takes bot scopes as a comma-separated list
    env_prefix="SLACK",
)
