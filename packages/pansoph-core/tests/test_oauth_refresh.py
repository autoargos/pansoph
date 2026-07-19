"""Hermetic tests for the sync-time OAuth access-token refresh.

An OAuth access token expires (~1h), so before each sync the runner mints a fresh one
from the sealed refresh token and overrides the connector's OAuth field, keeping every
OAuth connector working past expiry with no per-connector change. These tests pin the
three branches: no OAuth field, no sealed refresh token, and a successful mint. The
CredentialProvider, secret store, provider registry, and connector spec are all faked, so
there is no network and no Postgres. No em dashes anywhere.
"""

from __future__ import annotations

from typing import Any

from pansoph_connector_sdk import AccessToken
from pansoph_core import connectors as C


class _Kind:
    def __init__(self, value: str) -> None:
        self.value = value


class _Field:
    def __init__(
        self, kind: str, key: str, provider: str = "", scopes: tuple[str, ...] = ()
    ) -> None:
        self.kind = _Kind(kind)
        self.key = key
        self.oauth_provider = provider
        self.oauth_scopes = scopes


class _Spec:
    def __init__(self, fields: list[_Field]) -> None:
        self.fields = fields


class _Store:
    """A secret store where the refresh token is present iff `has_refresh`."""

    def __init__(self, has_refresh: bool) -> None:
        self._has_refresh = has_refresh

    async def get_secret(self, tenant: str, name: str) -> bytes | None:
        if "refresh_token" in name:
            return b"rt" if self._has_refresh else None
        return b"secret"


class _Provider:
    token_url = "https://oauth.example.com/token"

    async def client_id(self, store: Any) -> str:
        return "cid"


class _TokenSource:
    async def get_access_token(self, *, force_refresh: bool = False) -> AccessToken:
        return AccessToken(value="fresh-access-token")


class _CredProvider:
    def token_source(self, tenant_id: str, requirement: Any) -> _TokenSource:
        return _TokenSource()


def _wire(monkeypatch: Any, *, spec: _Spec | None, has_refresh: bool) -> None:
    monkeypatch.setattr(C, "_connector_spec", lambda _name: spec)
    monkeypatch.setattr(C.assembly, "build_secret_store", lambda: _Store(has_refresh))
    monkeypatch.setattr(C.assembly, "build_credential_provider", lambda: _CredProvider())
    monkeypatch.setattr("pansoph_core.oauth.get_provider", lambda _name: _Provider())


_INSTANCE = {"connector": "gmail", "tenant_id": "default", "id": "gmail"}


async def test_refresh_mints_and_overrides_the_oauth_field(monkeypatch: Any) -> None:
    _wire(monkeypatch, spec=_Spec([_Field("oauth", "token", "google", ("s",))]), has_refresh=True)
    out = await C._refresh_oauth_token(_INSTANCE, {"token": "stale", "other": "x"})
    assert out["token"] == "fresh-access-token"  # the stale token is replaced
    assert out["other"] == "x"  # the rest of the config is preserved


async def test_no_oauth_field_leaves_config_untouched(monkeypatch: Any) -> None:
    _wire(monkeypatch, spec=_Spec([_Field("string", "base_url")]), has_refresh=True)
    cfg = {"base_url": "https://x", "token": "abc"}
    assert await C._refresh_oauth_token(_INSTANCE, cfg) == cfg


async def test_no_sealed_refresh_token_leaves_config_untouched(monkeypatch: Any) -> None:
    # A pasted static token, or a provider that granted no refresh token: nothing to mint.
    _wire(monkeypatch, spec=_Spec([_Field("oauth", "token", "google")]), has_refresh=False)
    cfg = {"token": "static-pasted-token"}
    assert await C._refresh_oauth_token(_INSTANCE, cfg) == cfg
