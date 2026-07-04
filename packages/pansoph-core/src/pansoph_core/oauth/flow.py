"""The OAuth connect flow: authorize, then handle_callback.

This is the reusable core the routes call. It is provider-agnostic: it resolves an
`OAuthProvider` from the registry, uses it to build the consent URL and to exchange
the returned code, and seals the resulting tokens into the named connector
instance's config through the existing SecretStore (`seal_config`), creating or
updating the instance. It never stores a plaintext token in the config column, the
same invariant every connector secret follows.

Two entry points:
  - authorize(...) -> the provider consent URL to 302 to, after recording a
    single-use state (and a PKCE verifier where the provider supports it).
  - handle_callback(...) -> validate the state, exchange the code, seal the tokens
    into the instance config, returning the instance id the route redirects with.

The instance config keys the tokens are sealed under are derived from the
connector's OAUTH ConfigField (the field whose kind is OAUTH): the access token
takes that field's key, the refresh token takes `{key}__refresh_token`. The OAUTH
field is already a SECRET_KIND, so create_instance's own sealing pass keeps it out
of plaintext on every later edit too.

No em dashes anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..secrets import seal_config
from .errors import OAuthError
from .providers.base import new_pkce_verifier, pkce_challenge_s256
from .registry import get_provider
from .state import OAuthState, StateStore

if TYPE_CHECKING:
    import httpx
    from pansoph_connector_sdk import SecretStore

    from ..connectors import ConnectorManager


@dataclass(frozen=True)
class OAuthFlow:
    """Bundles the collaborators the flow needs, injected for hermetic testing.

    - manager: creates/updates the connector instance and reads the connector spec.
    - state_store: the single-use state store (Postgres in prod, in-memory in test).
    - secret_store: where the sealed tokens land.
    - http: the client used for the token exchange (respx-mockable in tests).
    - public_base_url: the externally reachable base URL used to build redirect_uri.
    """

    manager: ConnectorManager
    state_store: StateStore
    secret_store: SecretStore
    http: httpx.AsyncClient
    public_base_url: str

    def _redirect_uri(self, provider: str) -> str:
        """The callback URL for a provider, from the configured public base URL.

        Must match the redirect URI registered with the provider exactly, so it is
        derived from one configured base (PANSOPH_PUBLIC_BASE_URL) and the fixed
        callback path, never guessed from request headers (which a proxy can forge).
        """
        base = self.public_base_url.rstrip("/")
        return f"{base}/api/oauth/{provider}/callback"

    async def authorize(
        self,
        *,
        provider: str,
        connector: str,
        instance: str,
        tenant: str,
        scopes: tuple[str, ...] | None = None,
    ) -> str:
        """Build the provider consent URL and record the single-use state.

        Resolves the provider, resolves its client id (env or secret store),
        generates a PKCE verifier/challenge when the provider supports it, stores an
        `OAuthState` under a fresh token, and returns the authorize URL to 302 to.
        Raises the OAuth error family on an unknown or unconfigured provider.
        """
        prov = get_provider(provider)
        client_id = await prov.client_id(self.secret_store)
        redirect_uri = self._redirect_uri(provider)

        code_verifier: str | None = None
        code_challenge: str | None = None
        if prov.uses_pkce:
            code_verifier = new_pkce_verifier()
            code_challenge = pkce_challenge_s256(code_verifier)

        token = await self.state_store.put(
            OAuthState(
                provider=provider,
                connector=connector,
                instance=instance,
                tenant=tenant,
                redirect_uri=redirect_uri,
                code_verifier=code_verifier,
            )
        )
        return prov.build_authorize_url(
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=token,
            scopes=scopes,
            code_challenge=code_challenge,
        )

    async def handle_callback(self, *, provider: str, code: str, state: str) -> str:
        """Validate the callback, exchange the code, seal the tokens, return the id.

        Steps:
          1. Consume the state (single use). A missing/expired/replayed state raises
             StateError, so a forged callback is rejected.
          2. Cross-check the provider slug in the URL against the one bound to the
             state, so a state minted for one provider cannot be used at another.
          3. Exchange the code for tokens at the provider token endpoint.
          4. Seal the access token (and refresh token, if any) into the connector
             instance config and create/update the instance.
        Returns the instance id, which the route uses to redirect to
        /connectors?connected={instanceId}.
        """
        from .errors import StateError

        record = await self.state_store.consume(state)
        if record is None:
            raise StateError("oauth state is unknown, expired, or already used")
        if record.provider != provider:
            raise StateError(
                f"oauth state was minted for provider {record.provider!r}, not {provider!r}"
            )

        prov = get_provider(provider)
        client_id = await prov.client_id(self.secret_store)
        client_secret = await prov.client_secret(self.secret_store)
        result = await prov.exchange_code(
            http=self.http,
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=record.redirect_uri,
            code_verifier=record.code_verifier,
        )

        # Mirror the provider client_secret into the secret store under the reserved
        # _oauth tenant so the sync-time REFRESH_TOKEN minter (which resolves
        # client_secret from the store, not the env) can refresh this credential.
        # Idempotent: re-connecting just overwrites the same handle.
        await self.secret_store.set_secret(
            "_oauth", f"{provider}/client_secret", client_secret.encode("utf-8")
        )

        await self._seal_into_instance(
            record, result_access=result.access_token, result_refresh=result.refresh_token
        )
        return record.instance

    async def _seal_into_instance(
        self, record: OAuthState, *, result_access: str, result_refresh: str | None
    ) -> None:
        """Seal the minted tokens into the instance config and persist the instance.

        The access token is sealed under the connector's OAUTH field key; the
        refresh token, when present, under `{key}__refresh_token`. Both go through
        `seal_config`, so the config column holds only handles. Any config the
        instance already had is preserved and merged, so re-connecting refreshes the
        credential without dropping other settings.
        """
        oauth_key = self._oauth_field_key(record.connector)
        existing = await self.manager.get_instance(record.instance)
        config: dict[str, Any] = dict(existing["config"]) if existing else {}
        config[oauth_key] = result_access
        if result_refresh:
            config[f"{oauth_key}__refresh_token"] = result_refresh

        # Seal the two token fields into the secret store, replacing them in the
        # persisted config with handles, before the instance row is written.
        secret_keys = (oauth_key, f"{oauth_key}__refresh_token")
        sealed = await seal_config(
            self.secret_store, record.tenant, record.instance, config, secret_keys
        )

        name = existing["name"] if existing else record.instance
        await self.manager.create_instance(
            instance_id=record.instance,
            connector=record.connector,
            name=name,
            tenant_id=record.tenant,
            config=sealed,
            schedule_seconds=existing["schedule_seconds"] if existing else 0,
            schedule_cron=existing.get("schedule_cron") or "" if existing else "",
            scope_type=existing["scope_type"] if existing else "org",
            scope_ref=existing.get("scope_ref") or "" if existing else "",
        )

    def _oauth_field_key(self, connector: str) -> str:
        """The connector's OAUTH-kind config field key, the slot the token seals into.

        Reads the connector's ConnectorSpec and returns the first field whose kind
        is OAUTH. Falls back to "oauth_token" when the connector declares no spec or
        no OAUTH field, so a minimally-declared connector still connects. Raising is
        avoided here on purpose: the token must land somewhere sealed.
        """
        from ..connectors import _connector_spec  # local import avoids a cycle

        spec = _connector_spec(connector)
        if spec is not None:
            for f in spec.fields:
                if f.kind.value == "oauth":
                    return f.key
        return "oauth_token"


class OAuthFlowError(OAuthError):
    """Reserved for flow-level composition errors distinct from provider/state ones."""
