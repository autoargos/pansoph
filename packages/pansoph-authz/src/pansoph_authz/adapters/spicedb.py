"""SpiceDB adapter for the AuthzPort. The default engine.

Maps write_tuples and delete_tuples to WriteRelationships, and bulk_check to
CheckBulk with a consistency of at_least_as_fresh(ZedToken). Requires the
`authzed` client (install pansoph-authz[spicedb]). This is a stub; the wiring
lands with the core assembly.
"""

from __future__ import annotations

from pansoph_authz.port import AuthzPort, ConsistencyToken, Principal, Tuple


class SpiceDBAuthz(AuthzPort):
    def __init__(self, endpoint: str, preshared_key: str) -> None:
        self._endpoint = endpoint
        self._preshared_key = preshared_key

    async def write_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:
        raise NotImplementedError("SpiceDB write lands with the core assembly")

    async def delete_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:
        raise NotImplementedError("SpiceDB delete lands with the core assembly")

    async def bulk_check(
        self,
        subject: Principal,
        permission: str,
        object_ids: list[str],
        at_least_as_fresh: ConsistencyToken | None = None,
    ) -> set[str]:
        raise NotImplementedError("SpiceDB CheckBulk lands with the core assembly")
