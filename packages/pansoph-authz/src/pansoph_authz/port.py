"""The AuthzPort: the entire surface pansoph needs from a ReBAC engine.

Kept deliberately tiny so SpiceDB, OpenFGA, and a future Postgres only engine can
all satisfy it, and so the hot path uses only bulk_check, never an enumerate all
accessible call.
"""

from __future__ import annotations

from typing import NewType, Protocol, runtime_checkable

from pydantic import BaseModel

# An opaque consistency token (a SpiceDB ZedToken, or an OpenFGA snapshot marker).
# Stored alongside a document's acl_version and threaded through reads so a
# revocation is never silently stale.
ConsistencyToken = NewType("ConsistencyToken", str)


class Principal(BaseModel):
    """A subject or a group member. `ns` is the per connector instance namespace."""

    type: str  # "user" | "group"
    id: str
    ns: str = ""

    def ref(self) -> str:
        return f"{self.ns}/{self.type}:{self.id}" if self.ns else f"{self.type}:{self.id}"


class Relation(str):
    """A relation name, e.g. "reader", "member", "parent"."""


class Tuple(BaseModel):
    """A relationship tuple: subject has relation on object.

    Example: user:alice is "reader" of document:github:org1:42.
    """

    subject: Principal
    relation: str
    object_type: str
    object_id: str
    # Namespace for the object, used when the object is itself a namespaced principal (a
    # group in a membership tuple). Each adapter combines (object_ns, object_id) with the
    # SAME encoding it uses for a namespaced subject, so a group referenced as a subject
    # (in a reader tuple) and as an object (in a membership tuple) resolve to one entity.
    # Empty for plain resources like documents.
    object_ns: str = ""


@runtime_checkable
class AuthzPort(Protocol):
    async def write_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:
        """Create or update relationship tuples. Returns a token for read after write."""
        ...

    async def delete_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:
        """Delete relationship tuples (a revocation). Returns a fresh token."""
        ...

    async def bulk_check(
        self,
        subject: Principal,
        permission: str,
        object_ids: list[str],
        at_least_as_fresh: ConsistencyToken | None = None,
    ) -> set[str]:
        """Return the subset of object_ids the subject has `permission` on.

        This is the only call on the retrieval hot path. Implementations should map
        it to SpiceDB CheckBulk or OpenFGA BatchCheck. `at_least_as_fresh` requests
        a read no staler than the given token, which is how revocation freshness is
        guaranteed.
        """
        ...
