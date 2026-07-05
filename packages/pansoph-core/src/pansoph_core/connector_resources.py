"""Resource discovery and credential status for a connector instance.

Two reads sit on top of an already-connected (possibly draft) instance:

  - list_instance_resources: build the connector against its resolved credential and
    call the contract's `list_resources`, so the configure UI can enumerate the orgs,
    repos, drives, spaces, or channels the credential can actually reach. This is a
    read the credential already permits, never a scope escalation, and it reuses the
    same TokenSource as sync (`_build_connector`/`_attach_auth`).
  - instance_credential_status: report whether a valid credential exists for an
    instance, computed from the sealed secret handles in its config plus the
    connector's oauth field, so the UI stops re-prompting for a credential that is
    already present.

Both operate off the connectors module's building blocks, so nothing here is
connector-specific. No em dashes anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pansoph_types import Resource, ResourcePage

from .connectors import (
    STATUS_ACTIVE,
    STATUS_DRAFT,
    ConnectorManager,
    _build_connector,
    _connector_spec,
    _resolved_config,
)
from .secrets import secret_handle


class ResourceDiscoveryError(Exception):
    """A resource discovery call failed. `status_code` maps it to an HTTP response:
    400 when the connector declares no discovery, 502 when the upstream source erred.
    The message is safe to surface, it never carries a secret."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class CredentialStatus:
    """The shape the credential-status endpoint returns.

    - connected: a usable credential handle is sealed into the instance config.
    - provider: the oauth provider slug the credential belongs to, when known.
    - account_label: a human label for the connected account, when the connector or
      config records one; None when unknown (the UI falls back to the provider name).
    - expires_at: ISO-8601 access-token expiry, when the flow recorded one; None when
      the credential does not expire or expiry is not tracked yet.
    - has_refresh: a refresh token is sealed, so an expired access token can be
      renewed without a fresh consent.
    - needs_reauth: no valid credential exists (not connected), or the access token is
      known-expired with no refresh token. The UI shows the prominent Connect button
      only when this is True.
    """

    connected: bool
    provider: str | None
    account_label: str | None
    expires_at: str | None
    has_refresh: bool
    needs_reauth: bool


def _oauth_field(connector: str) -> Any | None:
    """The connector's OAUTH-kind ConfigField, or None when it declares no spec or no
    oauth field. Used to find the config key the credential seals into and the
    provider it belongs to."""
    spec = _connector_spec(connector)
    if spec is None:
        return None
    for field in spec.fields:
        if getattr(field.kind, "value", field.kind) == "oauth":
            return field
    return None


def _is_sealed(value: Any) -> bool:
    """True when a config value is a sealed secret handle (a real credential), not an
    empty placeholder."""
    return secret_handle(value) is not None


async def list_instance_resources(
    manager: ConnectorManager,
    instance: dict[str, Any],
    *,
    kind: str | None = None,
    parent_id: str | None = None,
    query: str | None = None,
    cursor: str | None = None,
) -> ResourcePage:
    """List the resources an instance's credential can reach, for a picker.

    Resolves the sealed secrets in the instance config, builds the connector with its
    refreshing TokenSource (reusing the sync path), and calls `list_resources`. A
    connector that does not declare `resource_discovery` (or does not implement the
    method) raises ResourceDiscoveryError(status_code=400). Any upstream failure is
    wrapped as status_code=502 so it is mapped to a 502 and never swallowed. When
    `query` is set and the connector has no server-side search, the page items are
    filtered here by a case-insensitive label/id match, so the picker's search box
    works uniformly across sources.
    """
    caps = _instance_capabilities(instance)
    if not getattr(caps, "resource_discovery", False):
        raise ResourceDiscoveryError(
            f"connector {instance['connector']!r} does not support resource discovery",
            status_code=400,
        )

    resolved = {**instance, "config": await _resolved_config(instance)}
    connector = _build_connector(resolved)
    list_resources = getattr(connector, "list_resources", None)
    if list_resources is None:
        raise ResourceDiscoveryError(
            f"connector {instance['connector']!r} declares resource_discovery but does "
            "not implement list_resources",
            status_code=400,
        )

    try:
        page = await list_resources(kind=kind, parent_id=parent_id, cursor=cursor)
    except ResourceDiscoveryError:
        raise
    except Exception as exc:  # noqa: BLE001  map any upstream error to a 502, never swallow
        raise ResourceDiscoveryError(
            f"resource discovery failed upstream: {exc}", status_code=502
        ) from exc

    if not isinstance(page, ResourcePage):
        raise ResourceDiscoveryError(
            "connector list_resources did not return a ResourcePage", status_code=502
        )

    if query:
        page = _filter_resources(page, query)
    return page


def _filter_resources(page: ResourcePage, query: str) -> ResourcePage:
    """Server-side substring filter for connectors without a native search, applied to
    the current page only. Pagination is preserved: the cursor still advances through
    the source's own pages, this just narrows what a page shows."""
    needle = query.casefold()

    def _matches(resource: Resource) -> bool:
        return needle in resource.label.casefold() or needle in resource.id.casefold()

    return ResourcePage(items=[r for r in page.items if _matches(r)], next_cursor=page.next_cursor)


def _instance_capabilities(instance: dict[str, Any]) -> Any:
    """The connector type's declared Capabilities, read from a cheap probe instance.

    Built with the stored (still sealed) config: capabilities are static connector
    metadata, so no credential resolution is needed to read them."""
    connector = _build_connector(instance)
    return connector.meta.capabilities


def instance_credential_status(instance: dict[str, Any]) -> CredentialStatus:
    """Compute an instance's credential status from its sealed config, no I/O.

    `connected` is true when the connector's oauth field holds a sealed handle (the
    callback wrote one). `has_refresh` is true when the paired refresh handle is
    present. `provider` comes from the oauth field's declared provider. `expires_at`
    is read from a recorded expiry in config when present (the flow may record one
    later); today it is None, which the UI treats as "no known expiry". `needs_reauth`
    is true when there is no connected credential, or when a known expiry is in the
    past and no refresh token exists to renew it.

    Pure and synchronous: it never resolves the secret value itself, only inspects the
    handles, so a credential's plaintext never enters this computation.
    """
    config = instance.get("config") or {}
    field = _oauth_field(instance["connector"])
    oauth_key = field.key if field is not None else "oauth_token"
    provider = getattr(field, "oauth_provider", None) if field is not None else None

    connected = _is_sealed(config.get(oauth_key))
    has_refresh = _is_sealed(config.get(f"{oauth_key}__refresh_token"))

    # The flow does not record expiry yet, so expires_at stays None (no known expiry).
    # A recorded ISO string in config is honored if a later change writes one.
    expires_at = config.get(f"{oauth_key}__expires_at")
    expires_at = expires_at if isinstance(expires_at, str) else None

    account_label = config.get(f"{oauth_key}__account_label")
    account_label = account_label if isinstance(account_label, str) else None

    needs_reauth = not connected or _is_expired_without_refresh(expires_at, has_refresh)

    return CredentialStatus(
        connected=connected,
        provider=provider,
        account_label=account_label,
        expires_at=expires_at,
        has_refresh=has_refresh,
        needs_reauth=needs_reauth,
    )


def _is_expired_without_refresh(expires_at: str | None, has_refresh: bool) -> bool:
    """True when a known access-token expiry is in the past and no refresh token exists
    to renew it. Unknown expiry (None) is never treated as expired, so a connector
    that does not record expiry is not falsely flagged for reauth."""
    if expires_at is None or has_refresh:
        return False
    from datetime import UTC, datetime

    try:
        exp = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    return exp <= datetime.now(UTC)


# Statuses re-exported so a caller (routes) has one import site for the lifecycle
# constants alongside the discovery helpers.
__all__ = [
    "STATUS_ACTIVE",
    "STATUS_DRAFT",
    "CredentialStatus",
    "ResourceDiscoveryError",
    "instance_credential_status",
    "list_instance_resources",
]
