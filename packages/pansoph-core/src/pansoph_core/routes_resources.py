"""Resource discovery and credential-status HTTP routes for a connector instance.

These two reads back the connect-then-pick UX: the picker enumerates what a
credential can reach, and the credential-status read stops the UI re-prompting when a
valid credential already exists. They live in their own router (included by the app
alongside the main one) so the connector surface stays under the file-size limit and
the discovery endpoints are isolated. Gated to a connector admin, audited, and mapping
upstream errors honestly (400 no-discovery, 502 upstream). No em dashes anywhere.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .audit import audit
from .routes import _CONNECTOR_ADMIN, _manager

router = APIRouter(prefix="/api")


@router.get("/connectors/instances/{instance_id}/resources")
async def instance_resources(
    instance_id: str,
    kind: str | None = None,
    parent: str | None = None,
    q: str | None = None,
    cursor: str | None = None,
    user: Any = Depends(_CONNECTOR_ADMIN),
) -> dict[str, Any]:
    """List the resources an instance's credential can reach, for the configure picker.

    Resolves the (possibly draft) instance, resolves its sealed secrets, builds the
    connector with the same TokenSource sync uses, and calls list_resources at the
    given (kind, parent) level with cursor pagination. `q` filters server-side when the
    source has no native search. 400 when the connector declares no discovery, 502 when
    the upstream source erred (never swallowed). Gated to a connector admin, audited.
    Returns {items: [Resource...], next_cursor: str | None}.
    """
    from .connector_resources import ResourceDiscoveryError, list_instance_resources

    manager = _manager()
    instance = await manager.get_instance(instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail=f"instance {instance_id!r} not found")
    try:
        page = await list_instance_resources(
            manager, instance, kind=kind, parent_id=parent, query=q, cursor=cursor
        )
    except ResourceDiscoveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    audit(
        "connector_resources",
        actor=user.id,
        tenant=instance["tenant_id"],
        instance=instance_id,
        kind=kind or "",
        count=len(page.items),
    )
    return {
        "items": [r.model_dump() for r in page.items],
        "next_cursor": page.next_cursor,
    }


@router.get("/connectors/instances/{instance_id}/credential")
async def instance_credential(
    instance_id: str, user: Any = Depends(_CONNECTOR_ADMIN)
) -> dict[str, Any]:
    """Report whether an instance already holds a valid credential, so the UI stops
    re-prompting. Computed from the sealed secret handles and the connector's oauth
    field, no secret plaintext is touched. Gated to a connector admin, audited.
    Returns {connected, provider, account_label?, expires_at?, has_refresh, needs_reauth}.
    """
    from .connector_resources import instance_credential_status

    manager = _manager()
    instance = await manager.get_instance(instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail=f"instance {instance_id!r} not found")
    status = instance_credential_status(instance)
    audit(
        "connector_credential",
        actor=user.id,
        tenant=instance["tenant_id"],
        instance=instance_id,
        connected=status.connected,
    )
    return {
        "connected": status.connected,
        "provider": status.provider,
        "account_label": status.account_label,
        "expires_at": status.expires_at,
        "has_refresh": status.has_refresh,
        "needs_reauth": status.needs_reauth,
    }
