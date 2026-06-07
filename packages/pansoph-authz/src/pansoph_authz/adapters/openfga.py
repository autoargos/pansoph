"""OpenFGA adapter for the AuthzPort. The first class low ops alternate.

Maps bulk_check to BatchCheck. Note the 2025 to 2026 BatchCheck regression and CVE:
pin and load test with a realistic tuple distribution. Requires the `openfga-sdk`
(install pansoph-authz[openfga]). Stub; wiring lands with the core assembly.
"""

from __future__ import annotations

from pansoph_authz.port import AuthzPort, ConsistencyToken, Principal, Tuple


class OpenFGAAuthz(AuthzPort):
    def __init__(self, api_url: str, store_id: str) -> None:
        self._api_url = api_url
        self._store_id = store_id

    async def write_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:
        raise NotImplementedError("OpenFGA write lands with the core assembly")

    async def delete_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:
        raise NotImplementedError("OpenFGA delete lands with the core assembly")

    async def bulk_check(
        self,
        subject: Principal,
        permission: str,
        object_ids: list[str],
        at_least_as_fresh: ConsistencyToken | None = None,
    ) -> set[str]:
        raise NotImplementedError("OpenFGA BatchCheck lands with the core assembly")
