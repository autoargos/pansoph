"""Bridge the sealed OAuth tokens into a sync-time AuthRequirement.

After the connect flow seals an access token (and a refresh token) into a
connector instance's config, the running sync must present a valid bearer token and
refresh it when it nears expiry. That is exactly what the shared CredentialProvider
does, but only for a connector that declares an `auth_requirement()`. This module
gives an oauth connector a one-liner to declare that requirement from its own OAUTH
field key and the provider's token endpoint, connecting the previously-unused
REFRESH_TOKEN minter path to real use.

An oauth connector implements `auth_requirement()` roughly as:

    from pansoph_core.oauth.auth_requirement import oauth_refresh_requirement

    def auth_requirement(self):
        return oauth_refresh_requirement(
            provider="google",
            tenant_id=self._tenant_id,
            instance_id=self._instance_id,
            oauth_field_key="oauth_token",
        )

The secret handles the flow wrote (`{instance}/{oauth_field_key}` for the access
token and `{instance}/{oauth_field_key}__refresh_token` for the refresh token) are
the same names this builds SecretRefs for, so the provider resolves them directly.

No em dashes anywhere.
"""

from __future__ import annotations

from pansoph_connector_sdk import AuthRequirement, GrantType, SecretRef

from .registry import get_provider


def oauth_refresh_requirement(
    *,
    provider: str,
    tenant_id: str,
    instance_id: str,
    oauth_field_key: str = "oauth_token",
    scopes: frozenset[str] | None = None,
) -> AuthRequirement | None:
    """Build a REFRESH_TOKEN AuthRequirement for a connected oauth instance.

    Returns None when the provider has no client id configured or does not support
    refresh, so a connector's `auth_requirement()` can return it directly and fall
    back to its config token when refresh is not wired. The client_id is read from
    the provider's env var; client_secret and refresh_token are secret refs into the
    store, matching the REFRESH_TOKEN grant's required fields.

    The refresh token handle name matches what flow.py seals:
    `{instance}/{oauth_field_key}__refresh_token`. The provider's own token_url is
    the refresh endpoint. This is intentionally synchronous and side-effect free so
    it can be called from a connector's `auth_requirement()` on the hot wiring path.
    """
    import os

    prov = get_provider(provider)
    env_stem = (prov.env_prefix or prov.name).upper()
    client_id = os.getenv(f"PANSOPH_OAUTH_{env_stem}_CLIENT_ID", "")
    if not client_id:
        # No client id means refresh cannot run; the connector uses its stored token
        # verbatim until an operator configures the provider credentials.
        return None

    access_name = f"{instance_id}/{oauth_field_key}"
    refresh_name = f"{instance_id}/{oauth_field_key}__refresh_token"
    return AuthRequirement(
        grant=GrantType.REFRESH_TOKEN,
        token_url=prov.token_url,
        scopes=scopes or frozenset(prov.default_scopes),
        secret_refs={
            # client_secret lives under the provider-shared reserved tenant, so a
            # single registered app's secret serves every tenant's instances.
            "client_secret": SecretRef("_oauth", f"{prov.name}/client_secret"),
            "refresh_token": SecretRef(tenant_id, refresh_name),
            # The access token handle is not required by the grant, but naming it
            # documents where the current token lives for a connector that reads it.
            "access_token": SecretRef(tenant_id, access_name),
        },
        extra={"client_id": client_id},
    )
