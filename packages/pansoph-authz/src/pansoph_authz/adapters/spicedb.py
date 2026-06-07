"""SpiceDB adapter for the AuthzPort. The default engine.

Maps write_tuples and delete_tuples to WriteRelationships (TOUCH and DELETE), and
bulk_check to CheckBulkPermissions with a consistency of at_least_as_fresh(ZedToken).
The ZedToken is how a revocation is guaranteed not to read stale: the ingestion
layer stores the token returned by a write and threads it back into the read.

Schema (written on ensure_schema): a document has reader relationships to users, to
the public wildcard, and to group members; read is the reader permission. Group
membership is nested so a user who is a member of a parent group inherits access.

Principals are namespaced per connector instance by prefixing the object id, so two
instances never collide. The public wildcard is the one principal that is not
namespaced.

authzed is an optional dependency (install pansoph-authz[spicedb]). Verified against
the authzed-py v1 API.
"""

from __future__ import annotations

from typing import Any

from pansoph_authz.port import AuthzPort, ConsistencyToken, Principal, Tuple

SCHEMA = """
definition user {}

definition group {
    relation member: user | group#member
}

definition document {
    relation reader: user | user:* | group#member
    permission read = reader
}
"""


def _ns_id(principal: Principal) -> str:
    if principal.id == "*":
        return "*"
    return f"{principal.ns}_{principal.id}" if principal.ns else principal.id


def _safe_obj(object_id: str) -> str:
    """SpiceDB object ids may not contain ':'. Document ids do ("conn:inst:src"),
    so map ':' to '/', which SpiceDB allows. Applied symmetrically on write and
    check, and reversed when reading results, so callers keep their original ids."""
    return object_id.replace(":", "/")


class SpiceDBAuthz(AuthzPort):
    def __init__(self, endpoint: str, preshared_key: str) -> None:
        self._endpoint = endpoint
        self._preshared_key = preshared_key
        self._schema_written = False

    def _client(self) -> Any:
        from authzed.api.v1 import AsyncClient
        from grpcutil import insecure_bearer_token_credentials

        return AsyncClient(self._endpoint, insecure_bearer_token_credentials(self._preshared_key))

    async def ensure_schema(self) -> None:
        from authzed.api.v1 import WriteSchemaRequest

        if self._schema_written:
            return
        await self._client().WriteSchema(WriteSchemaRequest(schema=SCHEMA))
        self._schema_written = True

    def _relationship(self, tup: Tuple) -> Any:
        from authzed.api.v1 import ObjectReference, Relationship, SubjectReference

        resource = ObjectReference(object_type=tup.object_type, object_id=_safe_obj(tup.object_id))
        subject_type = "group" if tup.subject.type == "group" else "user"
        subject = SubjectReference(
            object=ObjectReference(object_type=subject_type, object_id=_ns_id(tup.subject)),
            # A group grants access through its members.
            optional_relation="member" if subject_type == "group" else "",
        )
        return Relationship(resource=resource, relation=tup.relation, subject=subject)

    async def _write(self, tuples: list[Tuple], operation: Any) -> ConsistencyToken:
        from authzed.api.v1 import RelationshipUpdate, WriteRelationshipsRequest

        await self.ensure_schema()
        updates = [
            RelationshipUpdate(operation=operation, relationship=self._relationship(t))
            for t in tuples
        ]
        resp = await self._client().WriteRelationships(WriteRelationshipsRequest(updates=updates))
        return ConsistencyToken(resp.written_at.token)

    async def write_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:
        from authzed.api.v1 import RelationshipUpdate

        return await self._write(tuples, RelationshipUpdate.Operation.OPERATION_TOUCH)

    async def delete_tuples(self, tuples: list[Tuple]) -> ConsistencyToken:
        from authzed.api.v1 import RelationshipUpdate

        return await self._write(tuples, RelationshipUpdate.Operation.OPERATION_DELETE)

    async def bulk_check(
        self,
        subject: Principal,
        permission: str,
        object_ids: list[str],
        at_least_as_fresh: ConsistencyToken | None = None,
    ) -> set[str]:
        from authzed.api.v1 import (
            CheckBulkPermissionsRequest,
            CheckBulkPermissionsRequestItem,
            CheckPermissionResponse,
            Consistency,
            ObjectReference,
            SubjectReference,
            ZedToken,
        )

        if not object_ids:
            return set()

        if at_least_as_fresh:
            consistency = Consistency(at_least_as_fresh=ZedToken(token=str(at_least_as_fresh)))
        else:
            consistency = Consistency(minimize_latency=True)

        subj = SubjectReference(
            object=ObjectReference(object_type="user", object_id=_ns_id(subject))
        )
        # Map the SpiceDB-safe object id back to the caller's original id.
        safe_to_original = {_safe_obj(oid): oid for oid in object_ids}
        items = [
            CheckBulkPermissionsRequestItem(
                resource=ObjectReference(object_type="document", object_id=safe),
                permission=permission,
                subject=subj,
            )
            for safe in safe_to_original
        ]
        resp = await self._client().CheckBulkPermissions(
            CheckBulkPermissionsRequest(consistency=consistency, items=items)
        )
        has = CheckPermissionResponse.Permissionship.PERMISSIONSHIP_HAS_PERMISSION
        allowed: set[str] = set()
        for pair in resp.pairs:
            if pair.HasField("item") and pair.item.permissionship == has:
                allowed.add(safe_to_original[pair.request.resource.object_id])
        return allowed
