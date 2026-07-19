"""The permission red team. These gate releases.

They run the real ingestion pipeline, ACL mappers, and retrieval enforcement with
in-memory I/O fakes, and assert the properties that must never break: a user can
only retrieve what they are allowed to, across users, tenants, and after a
revocation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# This test lives in a subdirectory; put tests/ on the path so the shared fakes import.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _fakes import FakeAuthz, FakeEmbedder, FakeVectorStore  # noqa: E402
from pansoph_authz import Principal, Tuple  # noqa: E402
from pansoph_connector_sdk import InMemoryConnector  # noqa: E402
from pansoph_ingestion.adapters_parser import DoclingParser  # noqa: E402
from pansoph_ingestion.pipeline import IngestionPipeline  # noqa: E402
from pansoph_retrieval.pipeline import retrieve  # noqa: E402
from pansoph_types import AccessControl  # noqa: E402


def _pipeline(
    connector: InMemoryConnector, store: FakeVectorStore, authz: FakeAuthz
) -> IngestionPipeline:
    return IngestionPipeline(
        connector=connector,
        parser=DoclingParser(),
        embedder=FakeEmbedder(),
        vector_store=store,
        authz=authz,
    )


async def _scenario() -> tuple[FakeVectorStore, FakeAuthz]:
    store = FakeVectorStore()
    authz = FakeAuthz(user_groups={"alice": {"g1"}, "bob": set(), "carol": {"g1"}})

    c1 = InMemoryConnector(instance_id="mem")
    c1.put("eng", "engineering only", AccessControl(allow_groups=["g1"]))
    c1.put("pub", "everyone can read", AccessControl(public=True))
    await _pipeline(c1, store, authz).sync(connector_name="memory", tenant_id="t1")

    c2 = InMemoryConnector(instance_id="mem2")
    c2.put("t2pub", "tenant two public", AccessControl(public=True))
    await _pipeline(c2, store, authz).sync(connector_name="memory", tenant_id="t2")
    return store, authz


async def _ids(
    store: FakeVectorStore, authz: FakeAuthz, user: str, groups: list[str], tenant: str
) -> set[str]:
    hits = await retrieve(
        query="anything",
        # A person is one principal per tenant, so the query subject is namespaced by
        # tenant, exactly as the real retrieval routes build it and as the reader and
        # membership tuples are written.
        subject=Principal(type="user", id=user, ns=tenant),
        tenant_id=tenant,
        subject_groups=groups,
        final_k=10,
        vector_store=store,
        embedder=FakeEmbedder(),
        authz=authz,
    )
    return {h.doc_id for h in hits}


@pytest.mark.permission
async def test_cross_user_isolation() -> None:
    store, authz = await _scenario()
    alice = await _ids(store, authz, "alice", ["g1"], "t1")
    bob = await _ids(store, authz, "bob", [], "t1")
    assert "memory:mem:eng" in alice and "memory:mem:pub" in alice
    assert "memory:mem:pub" in bob, "public doc visible to everyone"
    assert "memory:mem:eng" not in bob, "bob is not in g1 and must not see the eng doc"


@pytest.mark.permission
async def test_cross_tenant_isolation() -> None:
    store, authz = await _scenario()
    carol_t2 = await _ids(store, authz, "carol", ["g1"], "t2")
    assert carol_t2 == {"memory:mem2:t2pub"}
    assert "memory:mem:eng" not in carol_t2 and "memory:mem:pub" not in carol_t2


@pytest.mark.permission
async def test_deny_overrides_group_grant() -> None:
    # An explicit deny is applied as a final filter and must override a group grant
    # that would otherwise let the document through the authz check.
    store = FakeVectorStore()
    authz = FakeAuthz(user_groups={"alice": {"g1"}, "carol": {"g1"}})
    c = InMemoryConnector(instance_id="mem")
    c.put("secret", "sensitive", AccessControl(allow_groups=["g1"], deny=["alice"]))
    await _pipeline(c, store, authz).sync(connector_name="memory", tenant_id="t1")

    alice = await _ids(store, authz, "alice", ["g1"], "t1")
    carol = await _ids(store, authz, "carol", ["g1"], "t1")
    assert "memory:mem:secret" not in alice, "an explicit deny must override a group grant"
    assert "memory:mem:secret" in carol, "carol is in g1 and is not denied"


@pytest.mark.permission
async def test_revocation_is_reflected() -> None:
    store, authz = await _scenario()
    before = await _ids(store, authz, "alice", ["g1"], "t1")
    assert "memory:mem:eng" in before

    await authz.delete_tuples(
        [
            Tuple(
                # Group reader tuples are tenant scoped, so the revocation deletes the
                # tuple as it was actually written (ns = tenant), not an instance ns.
                subject=Principal(type="group", id="g1", ns="t1"),
                relation="reader",
                object_type="document",
                object_id="memory:mem:eng",
            )
        ]
    )
    after = await _ids(store, authz, "alice", ["g1"], "t1")
    assert "memory:mem:eng" not in after, "revoked document must disappear from results"
    assert "memory:mem:pub" in after, "unrelated access is unaffected"


@pytest.mark.permission
async def test_direct_user_grant_resolves() -> None:
    # Regression: a document shared with a specific user was silently retrievable by
    # nobody, because the reader tuple and the query subject used different namespaces.
    # A direct-user grant must reach its grantee and no one else.
    store, authz = FakeVectorStore(), FakeAuthz()
    c = InMemoryConnector(instance_id="mem")
    c.put("shared", "for alice only", AccessControl(allow_users=["alice"]))
    await _pipeline(c, store, authz).sync(connector_name="memory", tenant_id="t1")

    alice = await _ids(store, authz, "alice", [], "t1")
    bob = await _ids(store, authz, "bob", [], "t1")
    assert "memory:mem:shared" in alice, "the direct-user grantee must retrieve the doc"
    assert "memory:mem:shared" not in bob, "no one else may retrieve a direct-user doc"


@pytest.mark.permission
async def test_scope_narrows_public_source_to_individual() -> None:
    # The Liber case: an instance scoped to an individual guards a public source. Only the
    # scoped user retrieves its documents, even though the source ACL is public.
    store, authz = FakeVectorStore(), FakeAuthz()
    c = InMemoryConnector(instance_id="mem")
    c.put("readme", "public in the source", AccessControl(public=True))
    await _pipeline(c, store, authz).sync(
        connector_name="memory", tenant_id="t1", scope_type="individual", scope_ref="alice"
    )

    alice = await _ids(store, authz, "alice", [], "t1")
    bob = await _ids(store, authz, "bob", [], "t1")
    assert "memory:mem:readme" in alice, "the scoped user retrieves the guarded doc"
    assert "memory:mem:readme" not in bob, "a public source scoped to alice denies bob"


@pytest.mark.permission
async def test_individual_scope_grants_owner_despite_source_native_ids() -> None:
    # Regression: a personal connector's source ACL names source-native principals (an
    # Atlassian accountId, a Slack member id), never the pansoph scope_ref. The doc must
    # still be granted to its owner: the owner's own credential fetched it, so they are
    # already allowed to see it. Before the fix, "scope_ref in allow_users" compared across
    # identity namespaces, always failed, and granted the owner NONE of their own docs
    # (the reported "I do not have that information" on freshly synced Confluence pages).
    store, authz = FakeVectorStore(), FakeAuthz()
    c = InMemoryConnector(instance_id="mem")
    # The source ACL grants an opaque source account id, not "alice".
    c.put("page", "binding plane notes", AccessControl(allow_users=["605635b245a3bb00"]))
    await _pipeline(c, store, authz).sync(
        connector_name="memory", tenant_id="t1", scope_type="individual", scope_ref="alice"
    )

    alice = await _ids(store, authz, "alice", [], "t1")
    bob = await _ids(store, authz, "bob", [], "t1")
    account = await _ids(store, authz, "605635b245a3bb00", [], "t1")
    assert "memory:mem:page" in alice, "the owner retrieves their own synced doc"
    assert "memory:mem:page" not in bob, "individual scope stays private to the owner"
    assert "memory:mem:page" not in account, (
        "the source-native id is not a pansoph principal and grants nobody"
    )


@pytest.mark.permission
async def test_scope_never_widens() -> None:
    # Scope only narrows. A doc shared with alice, scoped to a group that was not in the
    # source ACL, is reachable by no one (safe closure), never widened to that group.
    store, authz = FakeVectorStore(), FakeAuthz()
    c = InMemoryConnector(instance_id="mem")
    c.put("doc", "shared with alice", AccessControl(allow_users=["alice"]))
    await _pipeline(c, store, authz).sync(
        connector_name="memory", tenant_id="t1", scope_type="group", scope_ref="eng"
    )

    alice = await _ids(store, authz, "alice", [], "t1")
    eng_member = await _ids(store, authz, "carol", ["eng"], "t1")
    assert "memory:mem:doc" not in alice, "group scope removes alice's unrelated direct grant"
    assert "memory:mem:doc" not in eng_member, (
        "scope never widens to a group absent from the source ACL"
    )
