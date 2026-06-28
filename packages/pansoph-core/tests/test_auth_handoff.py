"""The runtime auth handoff: a connector that declares an AuthRequirement gets a
refreshing TokenSource attached, while a legacy connector is left untouched. Fully
offline. No em dashes anywhere.
"""

from __future__ import annotations

import httpx
from pansoph_connector_sdk import AuthRequirement, CredentialProvider, GrantType, SecretRef
from pansoph_core.connectors import _attach_auth


class _FakeSecretStore:
    def __init__(self) -> None:
        self._d: dict[tuple[str, str], bytes] = {}

    async def set_secret(self, tenant_id: str, name: str, value: bytes) -> None:
        self._d[(tenant_id, name)] = value

    async def get_secret(self, tenant_id: str, name: str) -> bytes | None:
        return self._d.get((tenant_id, name))

    async def delete_secret(self, tenant_id: str, name: str) -> None:
        self._d.pop((tenant_id, name), None)


class _AuthConnector:
    """A connector that opts into the auth subsystem."""

    token_source = None

    def auth_requirement(self) -> AuthRequirement:
        return AuthRequirement(
            grant=GrantType.STATIC,
            token_url="",
            secret_refs={"token": SecretRef("default", "tok")},
        )


class _LegacyConnector:
    """A connector that does not know about the auth subsystem."""

    token_source = "untouched"


def test_attach_auth_sets_token_source_for_opt_in_connector() -> None:
    provider = CredentialProvider(_FakeSecretStore(), httpx.AsyncClient())
    c = _AuthConnector()
    _attach_auth(c, "default", provider=provider)
    # The connector now holds a bound TokenSource instead of a raw secret.
    assert c.token_source is not None
    assert c.token_source is not _AuthConnector.token_source


def test_attach_auth_leaves_legacy_connector_untouched() -> None:
    c = _LegacyConnector()
    # No auth_requirement on the connector, so the provider is never consulted and
    # the existing behavior is preserved.
    _attach_auth(c, "default", provider=None)
    assert c.token_source == "untouched"
