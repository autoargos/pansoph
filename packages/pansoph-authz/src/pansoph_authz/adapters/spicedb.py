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


# SpiceDB object ids only allow [a-zA-Z0-9/_|=+-]. Document ids carry ':' and '.'
# (e.g. "github:inst:README.md"), so encode any disallowed character as "=XX" hex,
# which is injective (no two distinct ids collide) and uses only legal characters.
_SAFE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/_|-+")

# The alphabet for a single principal COMPONENT (a namespace or an id). Underscore is the
# separator between the two components, so it must be escaped INSIDE a component, or the
# join is ambiguous: ns="acme_corp" id="bob" would collide with ns="acme" id="corp_bob".
# Underscores are legal in emails and tenant ids, so the collision is real. Dropping "_"
# from the component alphabet escapes it as =5f inside a component while the join stays a
# literal underscore, making (ns, id) injective.
_SAFE_COMPONENT_CHARS = _SAFE_CHARS - {"_"}


def _encode(text: str, alphabet: frozenset[str]) -> str:
    """Encode text to a valid SpiceDB object id using the given legal alphabet, hex
    escaping any other character as =XX. Injective for a fixed alphabet."""
    parts: list[str] = []
    for ch in text:
        if ch in alphabet:
            parts.append(ch)
        else:
            parts.append("".join(f"={byte:02x}" for byte in ch.encode("utf-8")))
    return "".join(parts)


def _safe_obj(object_id: str) -> str:
    """Encode a document id into a valid SpiceDB object id. Reversed via a lookup
    map on the way back, so callers keep their original ids."""
    # The public wildcard is a legal SpiceDB object id and a sentinel, not data to
    # encode: encoding it would turn `user:*` into `user:=2a` and break public grants.
    # A real document id is never the bare "*", so this is unambiguous.
    if object_id == "*":
        return "*"
    return _encode(object_id, _SAFE_CHARS)


def _safe_ns_id(principal: Principal) -> str:
    """Encode a principal into a valid, unambiguous SpiceDB subject object id. The
    namespace and id are each encoded (with the separator underscore escaped inside them)
    and joined with a literal underscore, so two principals with an underscore at a
    different boundary can never collide across tenants."""
    if principal.id == "*":
        return "*"
    if principal.ns:
        ns = _encode(principal.ns, _SAFE_COMPONENT_CHARS)
        pid = _encode(principal.id, _SAFE_COMPONENT_CHARS)
        return f"{ns}_{pid}"
    return _encode(principal.id, _SAFE_COMPONENT_CHARS)


class SpiceDBAuthz(AuthzPort):
    def __init__(self, endpoint: str, preshared_key: str) -> None:
        self._endpoint = endpoint
        self._preshared_key = preshared_key
        self._schema_written = False
        self._cached_client: Any = None
        self._channel: Any = None
        self._last_token: str | None = None

    def _client(self) -> Any:
        # gRPC refuses call credentials over a non-loopback insecure channel, which
        # is exactly the Docker case (agora -> spicedb:50051 over plaintext). So we
        # build an insecure channel and inject the bearer token as metadata via an
        # interceptor instead of using grpcutil's loopback-only credentials.
        if self._cached_client is not None:
            return self._cached_client

        import grpc
        from authzed.api.v1 import AsyncClient

        token = self._preshared_key

        class _BearerAuth(grpc.aio.UnaryUnaryClientInterceptor):  # type: ignore[misc]
            async def intercept_unary_unary(
                self, continuation: Any, details: Any, request: Any
            ) -> Any:
                metadata = grpc.aio.Metadata()
                for key, value in details.metadata or ():
                    metadata.add(key, value)
                metadata.add("authorization", f"Bearer {token}")
                new_details = grpc.aio.ClientCallDetails(
                    details.method,
                    details.timeout,
                    metadata,
                    details.credentials,
                    details.wait_for_ready,
                )
                return await continuation(new_details, request)

        self._channel = grpc.aio.insecure_channel(self._endpoint, interceptors=[_BearerAuth()])
        client = AsyncClient.__new__(AsyncClient)
        client.init_stubs(self._channel)
        self._cached_client = client
        return client

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
            # Subject ids go through the same encoding as resource ids: a principal id can
            # carry SpiceDB-illegal characters (an email subject has '@' and '.'), and the
            # write and the check must encode identically so they still match.
            object=ObjectReference(object_type=subject_type, object_id=_safe_ns_id(tup.subject)),
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
        # Remember the write token so a following check reads at_least_as_fresh,
        # guaranteeing read-after-write (this is the ZedToken consistency guarantee).
        self._last_token = resp.written_at.token
        return ConsistencyToken(self._last_token)

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

        token = str(at_least_as_fresh) if at_least_as_fresh else self._last_token
        if token:
            consistency = Consistency(at_least_as_fresh=ZedToken(token=token))
        else:
            consistency = Consistency(minimize_latency=True)

        subj = SubjectReference(
            # Same encoding as the reader/membership writes, so an email subject (with
            # '@' and '.') matches the tuples it was written into.
            object=ObjectReference(object_type="user", object_id=_safe_ns_id(subject))
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
