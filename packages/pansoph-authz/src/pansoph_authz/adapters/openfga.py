"""OpenFGA adapter for the AuthzPort. The first class low ops alternate.

Talks to OpenFGA's stable REST API over HTTP (no SDK version coupling). write and
delete map to POST /stores/{id}/write; bulk_check maps to POST /stores/{id}/batch-check,
which is the right primitive for the overfetch-then-filter hot path (never use
ListObjects there). OpenFGA has no causal consistency token, so the at_least_as_fresh
argument is accepted and ignored; SpiceDB is the default precisely because it does.

Operators load MODEL_DSL (or its JSON form) into their store once. Object ids may
contain ':' in OpenFGA (only the leading type is split off), so document ids need no
rewriting here.

Note the 2025-2026 BatchCheck regression and CVE: pin and load test the server.
"""

from __future__ import annotations

import httpx

from pansoph_authz.port import AuthzPort, ConsistencyToken, Principal, Tuple

# The model an operator loads into OpenFGA. Mirrors the SpiceDB schema: a document
# reader can be a user, the public wildcard, or a group's members; read is reader.
MODEL_DSL = """
model
  schema 1.1
type user
type group
  relations
    define member: [user, group#member]
type document
  relations
    define reader: [user, user:*, group#member]
    define read: reader
"""


def _fga_user(p: Principal) -> str:
    if p.id == "*":
        return "user:*"
    ident = f"{p.ns}_{p.id}" if p.ns else p.id
    return f"group:{ident}#member" if p.type == "group" else f"user:{ident}"


class OpenFGAAuthz(AuthzPort):
    def __init__(self, api_url: str, store_id: str, model_id: str = "") -> None:
        self._api_url = api_url.rstrip("/")
        self._store_id = store_id
        self._model_id = model_id

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=f"{self._api_url}/stores/{self._store_id}", timeout=30.0)

    @staticmethod
    def _key(t: Tuple) -> dict[str, str]:
        # A namespaced object (a group) combines (object_ns, object_id) the same way
        # _fga_user combines a group subject's (ns, id), so a membership tuple's group
        # matches the group granted in a reader tuple. The object type comes from the
        # tuple (a membership tuple targets a group, a reader tuple targets a document),
        # not a hardcoded "document".
        ident = f"{t.object_ns}_{t.object_id}" if t.object_ns else t.object_id
        return {
            "user": _fga_user(t.subject),
            "relation": t.relation,
            "object": f"{t.object_type}:{ident}",
        }

    async def _write(self, tuples: list[Tuple], mode: str) -> ConsistencyToken:
        if not tuples:
            return ConsistencyToken("")
        body: dict[str, object] = {mode: {"tuple_keys": [self._key(t) for t in tuples]}}
        if self._model_id:
            body["authorization_model_id"] = self._model_id
        async with self._client() as client:
            resp = await client.post("/write", json=body)
            resp.raise_for_status()
        # OpenFGA has no consistency token.
        return ConsistencyToken("")

    async def write_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:
        return await self._write(tuples, "writes")

    async def delete_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:
        return await self._write(tuples, "deletes")

    async def bulk_check(
        self,
        subject: Principal,
        permission: str,
        object_ids: list[str],
        at_least_as_fresh: ConsistencyToken | None = None,
    ) -> set[str]:
        if not object_ids:
            return set()
        relation = "read" if permission == "read" else permission
        user = _fga_user(subject)
        checks = [
            {
                "tuple_key": {"user": user, "relation": relation, "object": f"document:{oid}"},
                "correlation_id": str(i),
            }
            for i, oid in enumerate(object_ids)
        ]
        body: dict[str, object] = {"checks": checks}
        if self._model_id:
            body["authorization_model_id"] = self._model_id
        async with self._client() as client:
            resp = await client.post("/batch-check", json=body)
            resp.raise_for_status()
            data = resp.json()
        # The field has been "result" and "results" across versions; accept either.
        results = data.get("result") or data.get("results") or {}
        allowed: set[str] = set()
        for i, oid in enumerate(object_ids):
            entry = results.get(str(i))
            if entry and entry.get("allowed"):
                allowed.add(oid)
        return allowed
