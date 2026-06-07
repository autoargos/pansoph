"""No-op authz adapter: access control switched off.

For open or single-tenant deployments where permissions are handled elsewhere or
are not needed. bulk_check allows every candidate, so retrieval returns everything
the vector pre-filter surfaced. This is the ADR's "ship without RBAC": pansoph runs
with no authz engine at all when configured with PANSOPH_AUTHZ_ADAPTER=none.

Choosing this is a deliberate, visible decision, not a silent default. SpiceDB
remains the default precisely because permission enforcement is the headline.
"""

from __future__ import annotations

from pansoph_authz.port import AuthzPort, ConsistencyToken, Principal, Tuple


class NoOpAuthz(AuthzPort):
    async def write_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:
        return ConsistencyToken("")

    async def delete_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:
        return ConsistencyToken("")

    async def bulk_check(
        self,
        subject: Principal,
        permission: str,
        object_ids: list[str],
        at_least_as_fresh: ConsistencyToken | None = None,
    ) -> set[str]:
        return set(object_ids)
