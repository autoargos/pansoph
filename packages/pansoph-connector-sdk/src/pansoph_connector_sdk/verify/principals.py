"""Synthetic principals and the visibility resolver used by the permission category.

The resolver mirrors the retrieval enforcement rule: a grant (public, group, or
user) makes a document visible, and an explicit deny is evaluated last and always
wins. Deny is a final filter here, never an authz exclusion tuple, matching how the
real retrieval path applies it.
"""

from __future__ import annotations

from pansoph_types import AccessControl
from pydantic import BaseModel, Field


class Principal(BaseModel):
    user_id: str
    groups: list[str] = Field(default_factory=list)


def can_see(acl: AccessControl, principal: Principal) -> bool:
    """Return whether `principal` may retrieve a document with this `acl`."""
    if principal.user_id in acl.deny:
        return False
    if acl.public:
        return True
    if principal.user_id in acl.allow_users:
        return True
    return any(g in acl.allow_groups for g in principal.groups)
