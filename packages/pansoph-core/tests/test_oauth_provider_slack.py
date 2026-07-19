"""Hermetic tests for the Slack OAuth v2 provider.

No network: the oauth.v2.access token exchange is mocked with respx, and the
authorize URL is asserted by parsing the query string. These cover the two ways
Slack departs from RFC 6749 (comma-separated bot scopes and the ok-envelope token
body) and the token-rotation refresh path. No em dashes anywhere.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from pansoph_connector_sdk import FieldKind
from pansoph_connector_slack.connector import SlackConnector
from pansoph_core.oauth.errors import CodeExchangeError
from pansoph_core.oauth.providers.slack import PROVIDER, SLACK_READ_SCOPES

_AUTHORIZE = "https://slack.com/oauth/v2/authorize"
_TOKEN = "https://slack.com/api/oauth.v2.access"  # noqa: S105  public endpoint


def test_provider_endpoints_and_flags_match_slack_docs() -> None:
    assert PROVIDER.name == "slack"
    assert PROVIDER.authorize_url == _AUTHORIZE
    assert PROVIDER.token_url == _TOKEN
    # Slack takes bot scopes as a comma-separated list, not the RFC space list.
    assert PROVIDER.scope_separator == ","
    # The confidential web-app flow authenticates with the client secret, not PKCE.
    assert PROVIDER.uses_pkce is False
    assert PROVIDER.env_prefix == "SLACK"
    # Default scopes are the read-only bot scopes the connector needs, nothing more.
    assert PROVIDER.default_scopes == SLACK_READ_SCOPES
    assert set(PROVIDER.default_scopes) == {
        "channels:read",
        "channels:history",
        "channels:join",
        "groups:read",
        "groups:history",
        "users:read",
    }


def test_slack_connector_declares_the_oauth_field_for_this_provider() -> None:
    # The connector wires the token field to this provider so the UI shows a Connect
    # button, while keeping the key "token" so the manual-paste path still works and
    # the flow seals the minted access token into the slot the connector reads.
    field = SlackConnector.spec().get("token")
    assert field is not None
    assert field.kind is FieldKind.OAUTH
    assert field.is_secret is True  # OAUTH values are sealed, never plaintext config
    assert field.oauth_provider == "slack"
    assert set(field.oauth_scopes) == set(SLACK_READ_SCOPES)


def test_build_authorize_url_uses_comma_scopes_and_no_pkce() -> None:
    url = PROVIDER.build_authorize_url(
        client_id="cid",
        redirect_uri="https://pansoph.example.com/api/oauth/slack/callback",
        state="st-abc",
        code_challenge="ignored-because-no-pkce",
    )
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert parsed.netloc == "slack.com"
    assert parsed.path == "/oauth/v2/authorize"
    assert qs["client_id"] == ["cid"]
    assert qs["redirect_uri"] == ["https://pansoph.example.com/api/oauth/slack/callback"]
    assert qs["state"] == ["st-abc"]
    assert qs["response_type"] == ["code"]
    # Scopes are joined with a comma into the single scope param (parse_qs already
    # url-decoded it), and there is no user_scope for a bot-only connector.
    assert qs["scope"] == [
        "channels:read,channels:history,channels:join,groups:read,groups:history,users:read"
    ]
    assert "user_scope" not in qs
    # uses_pkce is False, so no PKCE challenge leaks into the URL even if one is passed.
    assert "code_challenge" not in qs
    assert "code_challenge_method" not in qs


@respx.mock
async def test_exchange_code_unwraps_envelope_and_omits_grant_type() -> None:
    captured: dict[str, str] = {}

    def _record(request: httpx.Request) -> httpx.Response:
        captured.update(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(
            200,
            json={
                "ok": True,
                "access_token": "xoxb-bot-token",
                "token_type": "bot",
                "scope": "channels:read,channels:history",
                "bot_user_id": "U0KRQLJ9H",
                "team": {"id": "T9TK3CUKW", "name": "Acme"},
                "authed_user": {"id": "U1234"},
            },
        )

    respx.post(_TOKEN).mock(side_effect=_record)

    async with httpx.AsyncClient() as http:
        result = await PROVIDER.exchange_code(
            http=http,
            client_id="cid",
            client_secret="csecret",
            code="auth-code",
            redirect_uri="https://pansoph.example.com/api/oauth/slack/callback",
        )

    # The bot access token is the top-level access_token, unwrapped from the envelope.
    assert result.access_token == "xoxb-bot-token"
    assert result.token_type == "bot"
    assert result.scope == "channels:read,channels:history"
    # The team id is carried through so a connector can pin the workspace.
    assert result.extra == {"team_id": "T9TK3CUKW"}
    # Slack's initial exchange does NOT send grant_type; it sends exactly these.
    assert "grant_type" not in captured
    assert captured["client_id"] == "cid"
    assert captured["client_secret"] == "csecret"
    assert captured["code"] == "auth-code"
    assert captured["redirect_uri"] == "https://pansoph.example.com/api/oauth/slack/callback"


@respx.mock
async def test_exchange_code_rejects_ok_false_body() -> None:
    # Slack returns HTTP 200 even on failure, so success is decided by the ok flag.
    respx.post(_TOKEN).mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "invalid_code"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(CodeExchangeError, match="invalid_code"):
            await PROVIDER.exchange_code(
                http=http,
                client_id="cid",
                client_secret="csecret",
                code="bad",
                redirect_uri="https://pansoph.example.com/api/oauth/slack/callback",
            )


@respx.mock
async def test_refresh_rotates_token_via_refresh_grant() -> None:
    captured: dict[str, str] = {}

    def _record(request: httpx.Request) -> httpx.Response:
        captured.update(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(
            200,
            json={
                "ok": True,
                "access_token": "xoxb-new-token",
                "token_type": "bot",
                "refresh_token": "xoxe-1-new-refresh",
                "expires_in": 43200,
                "scope": "channels:read",
                "team": {"id": "T9TK3CUKW"},
            },
        )

    respx.post(_TOKEN).mock(side_effect=_record)

    async with httpx.AsyncClient() as http:
        result = await PROVIDER.refresh(
            http=http,
            client_id="cid",
            client_secret="csecret",
            refresh_token="xoxe-1-old-refresh",
        )

    # Token rotation returns a fresh access token, a rotated refresh token, and a
    # 12-hour lifetime, all carried through so the caller can persist and reschedule.
    assert result.access_token == "xoxb-new-token"
    assert result.refresh_token == "xoxe-1-new-refresh"
    assert result.expires_in == 43200
    # The refresh call uses grant_type=refresh_token with the current refresh token.
    assert captured["grant_type"] == "refresh_token"
    assert captured["refresh_token"] == "xoxe-1-old-refresh"
    assert captured["client_id"] == "cid"
    assert captured["client_secret"] == "csecret"
