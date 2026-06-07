"""The Identity port and its models."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class User(BaseModel):
    id: str
    email: str | None = None
    display_name: str | None = None
    tenant_id: str = ""
    # Direct group memberships. Transitive memberships are resolved by the port.
    group_ids: list[str] = Field(default_factory=list)


class Group(BaseModel):
    id: str
    name: str | None = None
    tenant_id: str = ""
    # Parent groups this group is a member of, for nested group resolution.
    parent_ids: list[str] = Field(default_factory=list)


@runtime_checkable
class IdentityPort(Protocol):
    async def get_user(self, user_id: str) -> User | None:
        """Return a user by id, or None."""
        ...

    async def groups_for_user(self, user_id: str) -> set[str]:
        """Return the transitive closure of the user's group memberships.

        The authz layer uses this to build the coarse subject group pre-filter, so
        it must already include nested parent groups.
        """
        ...

    async def authenticate(self, token: str) -> User | None:
        """Validate a bearer token and return the user, or None. For OIDC adapters."""
        ...
