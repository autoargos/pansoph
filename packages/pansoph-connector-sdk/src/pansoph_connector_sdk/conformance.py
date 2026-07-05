"""The shared connector conformance suite.

Any connector, native or declarative, must pass this. It exercises the contract,
incremental sync, deletions, and ACL mapping, and asserts the type contract the
runtime relies on. Import it from a connector's tests and call `assert_conforms`.

This is the harness. The exhaustive per-connector suites are task #21; this is the
single source of truth for what "conforms" means.
"""

from __future__ import annotations

from pansoph_types import AccessControl, DocRef, RawDoc, ResourcePage

from .contract import Connector, ValidationResult


async def assert_conforms(connector: Connector) -> None:
    """Raise AssertionError if `connector` violates the contract.

    Caller is responsible for seeding the connector with at least one document so
    list_changes has something to return.
    """
    meta = connector.meta
    assert meta.name, "connector meta must have a name"

    vr = connector.validate({})
    assert isinstance(vr, ValidationResult), "validate must return a ValidationResult"

    # First full pass: collect refs and cursors, assert their types.
    refs: list[DocRef] = []
    last_cursor: str | None = None
    async for ref, cursor in connector.list_changes(None):
        assert isinstance(ref, DocRef), "list_changes must yield DocRef"
        assert isinstance(cursor, str) and cursor, "list_changes must yield a non-empty cursor"
        assert ref.instance_id, "every DocRef must carry its instance_id for isolation"
        refs.append(ref)
        last_cursor = cursor

    assert refs, "seed the connector before conformance: list_changes returned nothing"

    # fetch returns a RawDoc plus its ACL, and the RawDoc matches the ref.
    raw, acl = await connector.fetch(refs[0])
    assert isinstance(raw, RawDoc), "fetch must return a RawDoc"
    assert isinstance(acl, AccessControl), "fetch must return an AccessControl"
    assert raw.source_doc_id == refs[0].source_doc_id, "fetched doc id must match the ref"

    # permission_sync connectors must answer list_acls with an AccessControl.
    if meta.capabilities.permission_sync:
        only_acl = await connector.list_acls(refs[0])
        assert isinstance(only_acl, AccessControl), "list_acls must return an AccessControl"

    # Incremental: replaying with the last cursor must yield no already-seen change.
    if meta.capabilities.incremental and last_cursor is not None:
        seen = {r.source_doc_id for r in refs}
        async for ref, _ in connector.list_changes(last_cursor):
            assert ref.source_doc_id not in seen, (
                "incremental cursor leaked an already-synced document"
            )

    # Resource discovery: a connector that declares the capability must implement
    # list_resources and return a ResourcePage, exactly as a deletions-capable
    # connector must surface a tombstone. This catches the "declares
    # resource_discovery=True but never lists a resource" defect at the gate.
    if meta.capabilities.resource_discovery:
        list_resources = getattr(connector, "list_resources", None)
        assert list_resources is not None, (
            "connector declares resource_discovery=True but does not implement "
            "list_resources; implement it or stop declaring the capability"
        )
        page = await list_resources()
        assert isinstance(page, ResourcePage), "list_resources must return a ResourcePage"
