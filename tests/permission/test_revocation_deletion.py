"""Phase 2 red team: deletion and revocation propagate end to end.

These run the real ingestion pipeline, ACL mappers, diff, and retrieval enforcement
with in-memory I/O fakes, and assert the Phase 2 guarantee: once a source document is
deleted (a tombstone) or a grant is revoked (an ACL contraction), a re-sync makes the
document unretrievable, gone from BOTH the vector store and the authz bulk check. This
is the ADR-0002 revocation guarantee and pansoph CLAUDE.md rule 1.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# This test lives in a subdirectory; put tests/ on the path so the shared fakes import.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _fakes import FakeAuthz, FakeEmbedder, FakeVectorStore  # noqa: E402
from pansoph_authz import Principal  # noqa: E402
from pansoph_connector_sdk import InMemoryConnector  # noqa: E402
from pansoph_ingestion.acl_log import InMemoryAclLog  # noqa: E402
from pansoph_ingestion.adapters_parser import DoclingParser  # noqa: E402
from pansoph_ingestion.pipeline import IngestionPipeline  # noqa: E402
from pansoph_retrieval.pipeline import retrieve  # noqa: E402
from pansoph_types import AccessControl  # noqa: E402

_TENANT = "t1"
_INSTANCE = "mem"


def _pipeline(
    connector: InMemoryConnector, store: FakeVectorStore, authz: FakeAuthz, acl_log: InMemoryAclLog
) -> IngestionPipeline:
    return IngestionPipeline(
        connector=connector,
        parser=DoclingParser(),
        embedder=FakeEmbedder(),
        vector_store=store,
        authz=authz,
        acl_log=acl_log,
    )


async def _retrieved_ids(
    store: FakeVectorStore, authz: FakeAuthz, user: str, groups: list[str]
) -> set[str]:
    hits = await retrieve(
        query="anything",
        subject=Principal(type="user", id=user, ns=_TENANT),
        tenant_id=_TENANT,
        subject_groups=groups,
        final_k=10,
        vector_store=store,
        embedder=FakeEmbedder(),
        authz=authz,
    )
    return {h.doc_id for h in hits}


def _in_store(store: FakeVectorStore, doc_id: str) -> bool:
    return any(r.doc_id == doc_id for r in store.rows)


@pytest.mark.permission
async def test_deletion_tombstone_purges_doc_and_tuples() -> None:
    # A source deletion (a tombstone from list_changes) must remove the document from
    # both the vector store and the authz tuples, so no one can retrieve it after re-sync.
    store, authz, acl_log = FakeVectorStore(), FakeAuthz(), InMemoryAclLog()
    c = InMemoryConnector(instance_id=_INSTANCE)
    c.put("doomed", "readable by alice", AccessControl(allow_users=["alice"]))
    await _pipeline(c, store, authz, acl_log).sync(connector_name="memory", tenant_id=_TENANT)

    doc_id = "memory:mem:doomed"
    assert doc_id in await _retrieved_ids(store, authz, "alice", []), "grantee sees it first"
    assert _in_store(store, doc_id)

    # The source removes it; the next incremental sync yields the tombstone.
    c.remove("doomed")
    await _pipeline(c, store, authz, acl_log).sync(connector_name="memory", tenant_id=_TENANT)

    assert not _in_store(store, doc_id), "deleted doc purged from the vector store"
    allowed = await authz.bulk_check(
        Principal(type="user", id="alice", ns=_TENANT), "reader", [doc_id]
    )
    assert doc_id not in allowed, "deleted doc's reader tuples removed from authz"
    assert doc_id not in await _retrieved_ids(store, authz, "alice", []), "unretrievable"


@pytest.mark.permission
async def test_revocation_of_direct_grant_on_resync() -> None:
    # A grant contraction: a doc first shared with alice and bob, then only bob, must stop
    # being retrievable by alice after a re-sync, while bob keeps access. The revoked
    # reader tuple is deleted (diff_acls), not left stale.
    store, authz, acl_log = FakeVectorStore(), FakeAuthz(), InMemoryAclLog()
    c = InMemoryConnector(instance_id=_INSTANCE)
    c.put("shared", "shared then narrowed", AccessControl(allow_users=["alice", "bob"]))
    await _pipeline(c, store, authz, acl_log).sync(connector_name="memory", tenant_id=_TENANT)

    doc_id = "memory:mem:shared"
    assert doc_id in await _retrieved_ids(store, authz, "alice", []), "alice sees it first"
    assert doc_id in await _retrieved_ids(store, authz, "bob", []), "bob sees it first"
    v1 = next(r.acl_version for r in store.rows if r.doc_id == doc_id)

    # The source revokes alice. Re-put with the contracted ACL and re-sync.
    c.put("shared", "shared then narrowed", AccessControl(allow_users=["bob"]))
    await _pipeline(c, store, authz, acl_log).sync(connector_name="memory", tenant_id=_TENANT)

    alice_allowed = await authz.bulk_check(
        Principal(type="user", id="alice", ns=_TENANT), "reader", [doc_id]
    )
    assert doc_id not in alice_allowed, "revoked reader tuple deleted from authz"
    assert doc_id not in await _retrieved_ids(store, authz, "alice", []), "alice cannot retrieve"
    assert doc_id in await _retrieved_ids(store, authz, "bob", []), "bob's grant is preserved"

    v2 = next(r.acl_version for r in store.rows if r.doc_id == doc_id)
    assert v2 == v1 + 1, "acl_version is monotonic and bumps on an ACL change"


@pytest.mark.permission
async def test_unchanged_acl_does_not_bump_version() -> None:
    # A re-sync with an identical ACL must not bump acl_version, so cache invalidation
    # keyed on the version does not fire spuriously.
    store, authz, acl_log = FakeVectorStore(), FakeAuthz(), InMemoryAclLog()
    c = InMemoryConnector(instance_id=_INSTANCE)
    c.put("stable", "same acl twice", AccessControl(allow_users=["alice"]))
    await _pipeline(c, store, authz, acl_log).sync(connector_name="memory", tenant_id=_TENANT)
    doc_id = "memory:mem:stable"
    v1 = next(r.acl_version for r in store.rows if r.doc_id == doc_id)

    c.put("stable", "same acl twice", AccessControl(allow_users=["alice"]))
    await _pipeline(c, store, authz, acl_log).sync(connector_name="memory", tenant_id=_TENANT)
    v2 = next(r.acl_version for r in store.rows if r.doc_id == doc_id)
    assert v2 == v1, "an unchanged ACL keeps the same acl_version"
