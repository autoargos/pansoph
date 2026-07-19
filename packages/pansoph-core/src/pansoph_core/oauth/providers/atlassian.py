"""Atlassian OAuth 2.0 (3LO) provider.

Docs verified 2026-07-04 against the current official Atlassian developer docs:
  Flow:       https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/
              https://developer.atlassian.com/cloud/confluence/oauth-2-3lo-apps/
  Jira scopes:       https://developer.atlassian.com/cloud/jira/platform/scopes-for-oauth-2-3LO-and-forge-apps/
  Confluence scopes: https://developer.atlassian.com/cloud/confluence/scopes-for-oauth-2-3LO-and-forge-apps/
  authorize:  https://auth.atlassian.com/authorize
  token:      https://auth.atlassian.com/oauth/token
  cloudid:    GET https://api.atlassian.com/oauth/token/accessible-resources

Verified facts from those docs:
  - The authorize URL REQUIRES audience=api.atlassian.com and, to force the consent
    screen (which is also required to obtain a refresh token on re-consent),
    prompt=consent. Both are set in extra_authorize_params below. The base builder
    adds response_type=code, client_id, redirect_uri, state, and the space-separated
    scope list.
  - This is a confidential-client web flow: client_id AND client_secret are sent to
    the token endpoint. Atlassian's 3LO docs do not describe or require PKCE for this
    flow, so uses_pkce stays False.
  - A refresh token is issued ONLY when `offline_access` is in the requested scopes;
    it is included in DEFAULT_SCOPES below. Atlassian issues ROTATING refresh tokens
    (each refresh returns a new one; the previous becomes invalid; a token expires
    after 90 days of inactivity), so the sync-time refresh path MUST persist the
    rotated token. The shared CredentialProvider already does this for REFRESH_TOKEN
    grants, and TokenResult.refresh_token carries the rotated value through.
  - Atlassian's token endpoint documents a JSON request body (Content-Type
    application/json), unlike the form-encoded body the base provider sends. This
    module overrides exchange_code and refresh to POST JSON to match the docs exactly.
    The token RESPONSE is standard RFC 6749 JSON, so the base parser is reused.
  - After the token exchange a client must call
    https://api.atlassian.com/oauth/token/accessible-resources to learn the cloudid,
    then address the API as https://api.atlassian.com/ex/jira/{cloudid}/... or
    https://api.atlassian.com/ex/confluence/{cloudid}/... . That discovery is a
    connector concern (it needs the connector's http client and instance config), so
    it lives in the connector, not here; this module only mints the token.

Read-only scopes (see DEFAULT_SCOPES): classic read:jira-work / read:jira-user cover
issue search and reads; granular read:project-role:jira covers the project-role
actors call the Jira ACL mapping makes. For Confluence: search:confluence covers the
CQL change feed (a distinct scope from content reads), read:confluence-content.all
covers page bodies, read:confluence-space.summary covers space info,
read:content.restriction:confluence covers content read-restrictions,
read:space:confluence covers space discovery, and read:space.permission:confluence
covers the space-wide View audience the ACL intersects. offline_access yields the
refresh token. Each connector narrows this to its own set via oauth_scopes.

Client credentials come from PANSOPH_OAUTH_ATLASSIAN_CLIENT_ID / _SECRET.

No em dashes anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .base import OAuthProvider, TokenResult

# The default read-only scope set, spanning both Atlassian connectors (jira,
# confluence). offline_access is required for a refresh token. A connector may
# narrow this via its OAUTH ConfigField.oauth_scopes; these are the union used
# when a connector does not override them.
DEFAULT_SCOPES: tuple[str, ...] = (
    # Jira: classic read scopes plus the granular project-role read the ACL uses.
    "read:jira-work",
    "read:jira-user",
    "read:project-role:jira",
    # Confluence: CQL search feed, page content (granular read:page:confluence, which the
    # gateway requires for content reads once any granular scope is present, plus the classic
    # read:confluence-content.all for a classic-only app), space summaries, content
    # restrictions, space discovery, and the space-wide View audience the ACL intersects.
    "search:confluence",
    "read:page:confluence",
    "read:confluence-content.all",
    "read:confluence-space.summary",
    "read:content.restriction:confluence",
    "read:space:confluence",
    "read:space.permission:confluence",
    # Refresh: Atlassian issues a (rotating) refresh token only with this scope.
    "offline_access",
)


@dataclass(frozen=True)
class _AtlassianProvider(OAuthProvider):
    """Atlassian override that POSTs a JSON token body, per the 3LO docs.

    The base provider sends an RFC 6749 form-encoded body, which Atlassian's
    Auth0-backed endpoint also accepts, but the official docs document a JSON body.
    This override sends JSON so the request matches the documented contract exactly.
    The response is standard RFC 6749 JSON, so parse_token_response (inherited) is
    unchanged, and CodeExchangeError still surfaces any non-2xx or tokenless body.
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
        """Exchange an authorization code for tokens using a JSON body.

        code_verifier is accepted for signature compatibility with the base flow
        but is unused: this 3LO flow is a confidential client and does not use PKCE.
        """
        payload: dict[str, str] = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        response = await http.post(
            self.token_url, json=payload, headers={"Accept": "application/json"}
        )
        if response.status_code >= 400:
            from ..errors import CodeExchangeError

            raise CodeExchangeError(
                f"provider {self.name!r} token endpoint returned "
                f"HTTP {response.status_code}: {response.text[:300]}"
            )
        return self.parse_token_response(response)

    async def refresh(
        self,
        *,
        http: httpx.AsyncClient,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> TokenResult:
        """Exchange a rotating refresh token for a fresh access token, JSON body.

        The returned TokenResult carries the NEW refresh_token Atlassian rotates in;
        the caller must persist it, since the old one is now invalid.
        """
        payload: dict[str, str] = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        }
        response = await http.post(
            self.token_url, json=payload, headers={"Accept": "application/json"}
        )
        if response.status_code >= 400:
            from ..errors import CodeExchangeError

            raise CodeExchangeError(
                f"provider {self.name!r} refresh returned "
                f"HTTP {response.status_code}: {response.text[:300]}"
            )
        return self.parse_token_response(response)


PROVIDER = _AtlassianProvider(
    name="atlassian",
    authorize_url="https://auth.atlassian.com/authorize",
    token_url="https://auth.atlassian.com/oauth/token",  # noqa: S106  public endpoint
    default_scopes=DEFAULT_SCOPES,
    uses_pkce=False,
    extra_authorize_params={"audience": "api.atlassian.com", "prompt": "consent"},
    env_prefix="ATLASSIAN",
)
