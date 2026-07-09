"""Admin routes for identity-group aliases: the operator declared bridge from an IdP
group to a connector source group.

A connector fails closed by granting a namespaced source group (slack:member:{instance},
jira:licensed, ...) that no identity is a member of. These routes let a connector admin
connect their own identity groups to those source groups, so the mapped users can retrieve
the documents and no one else. Gated to a connector admin, audited. No em dashes anywhere.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import assembly
from .audit import audit
from .routes import _CONNECTOR_ADMIN

router = APIRouter(prefix="/api/authz")


class AliasBody(BaseModel):
    tenant_id: str = "default"
    identity_group: str
    connector_group: str


def _store() -> Any:
    return assembly.build_group_alias_store()


@router.get("/group-aliases")
async def list_group_aliases(
    tenant_id: str = "default", user: Any = Depends(_CONNECTOR_ADMIN)
) -> dict[str, Any]:
    """Every identity-group to connector-group edge for a tenant."""
    return {"aliases": await _store().list_aliases(tenant_id)}


@router.get("/connector-groups")
async def connector_groups_in_use(
    tenant_id: str = "default", user: Any = Depends(_CONNECTOR_ADMIN)
) -> dict[str, Any]:
    """The distinct connector groups the tenant's indexed documents are actually granted
    to, so the admin aliases a real target instead of guessing a group id."""
    return {"connector_groups": await _store().connector_groups_in_use(tenant_id)}


@router.post("/group-aliases")
async def add_group_alias(body: AliasBody, user: Any = Depends(_CONNECTOR_ADMIN)) -> dict[str, Any]:
    """Declare that members of an identity group are also members of a connector group.
    Idempotent. Takes effect on the next query (the expansion is read per request)."""
    if not body.identity_group or not body.connector_group:
        raise HTTPException(status_code=400, detail="identity_group and connector_group required")
    await _store().add_alias(body.tenant_id, body.identity_group, body.connector_group)
    audit(
        "group_alias_add",
        actor=user.id,
        tenant=body.tenant_id,
        identity_group=body.identity_group,
        connector_group=body.connector_group,
    )
    return {"ok": True}


@router.delete("/group-aliases")
async def remove_group_alias(
    body: AliasBody, user: Any = Depends(_CONNECTOR_ADMIN)
) -> dict[str, Any]:
    """Remove an alias. A revocation: the connector group's documents stop being reachable
    through this identity group on the next query."""
    await _store().remove_alias(body.tenant_id, body.identity_group, body.connector_group)
    audit(
        "group_alias_remove",
        actor=user.id,
        tenant=body.tenant_id,
        identity_group=body.identity_group,
        connector_group=body.connector_group,
    )
    return {"ok": True}
