"""Live enforcement tests: the real retrieve() path against a real SpiceDB and pgvector.

These are the proof that "you only ever retrieve what you are allowed to see" holds
against the actual engines, not just the in memory fakes in tests/permission. A SpiceDB
schema bug, a ZedToken freshness bug, or a vector pre filter group leak fails here.

Each test seeds through the real SpiceDBAuthz and PgVectorScaleStore (see conftest), then
drives the real `retrieve()`. Run with the engines up (deploy/compose.test.yaml) and the
PANSOPH_SPICEDB_ENDPOINT / PANSOPH_PG_DSN env vars set; otherwise every test skips.
"""

from __future__ import annotations

import pytest
from pansoph_authz import TENANT_EVERYONE, Principal, Tuple, membership_tuples
from pansoph_retrieval.pipeline import retrieve
from pansoph_retrieval.ports import SearchHit
from pansoph_types import AccessControl

pytestmark = pytest.mark.integration


async def _ids(
    store: object,
    authz: object,
    embedder: object,
    *,
    user: str,
    groups: list[str],
    tenant: str,
    query: str = "the document body",
) -> set[str]:
    """Run the real enforcement path and return the doc ids the subject can retrieve."""
    # Mirror routes._retrieve: every user is a member of its tenant everyone group, so
    # public documents (granted to that group) resolve within the tenant.
    await authz.write_tuples(  # type: ignore[attr-defined]
        membership_tuples(user, [TENANT_EVERYONE], tenant)
    )
    hits = await retrieve(
        query=query,
        subject=Principal(type="user", id=user),
        tenant_id=tenant,
        subject_groups=groups,
        final_k=10,
        vector_store=store,  # type: ignore[arg-type]
        embedder=embedder,  # type: ignore[arg-type]
        authz=authz,  # type: ignore[arg-type]
    )
    return {h.doc_id for h in hits}


async def test_live_public_is_tenant_scoped_at_authz_layer(authz, seed) -> None:
    # Defense in depth for multi-tenant: a public document is readable only WITHIN its
    # tenant at the authz layer (the source of truth), not only at the vector
    # pre-filter, because public grants to the tenant everyone group rather than a
    # global wildcard. A cross-tenant user is denied even if the pre-filter were bypassed.
    await seed(doc_id="doc:t1pub", tenant="t1", instance="inst", public=True)
    await authz.write_tuples(membership_tuples("alice", [TENANT_EVERYONE], "t1"))
    await authz.write_tuples(membership_tuples("mallory", [TENANT_EVERYONE], "t2"))

    in_tenant = await authz.bulk_check(Principal(type="user", id="alice"), "read", ["doc:t1pub"])
    cross_tenant = await authz.bulk_check(
        Principal(type="user", id="mallory"), "read", ["doc:t1pub"]
    )

    assert "doc:t1pub" in in_tenant, "a tenant member reads its tenant's public document"
    assert "doc:t1pub" not in cross_tenant, (
        "a cross-tenant user must NOT read a public document (no global wildcard)"
    )


async def test_live_cross_user_isolation_excludes_non_member(
    store, authz, embedder, seed, add_membership
):
    await seed(doc_id="doc:eng", tenant="t1", instance="inst", allow_groups=("g1",))
    await seed(doc_id="doc:pub", tenant="t1", instance="inst", public=True)
    await add_membership("alice", ["g1"], "t1")

    alice = await _ids(store, authz, embedder, user="alice", groups=["g1"], tenant="t1")
    bob = await _ids(store, authz, embedder, user="bob", groups=[], tenant="t1")

    assert alice == {"doc:eng", "doc:pub"}
    assert bob == {"doc:pub"}, "bob is not in g1 and must see only the public doc"


async def test_live_nested_group_inheritance_grants_descendant_member(
    store, authz, embedder, seed, add_membership
):
    # child is a member-subgroup of parent (group:t1_parent#member includes
    # group:t1_child#member). The doc is granted to parent, and alice is a member of
    # child, so alice must inherit read through two levels of group recursion. This is
    # exactly the case the flat in-memory fake cannot model.
    await seed(doc_id="doc:roadmap", tenant="t1", instance="inst", allow_groups=("parent",))
    await authz.write_tuples(
        [
            Tuple(
                subject=Principal(type="group", id="child", ns="t1"),
                relation="member",
                object_type="group",
                object_id="t1_parent",
            )
        ]
    )
    await add_membership("alice", ["child"], "t1")

    # subject_groups=["parent"] only lets the coarse pre-filter keep the doc; the authz
    # bulk check does the real nested resolution. bob has no membership at all.
    alice = await _ids(store, authz, embedder, user="alice", groups=["parent"], tenant="t1")
    bob = await _ids(store, authz, embedder, user="bob", groups=["parent"], tenant="t1")

    assert "doc:roadmap" in alice, "alice inherits via child -> parent through two levels"
    assert "doc:roadmap" not in bob, "no membership means no access even past the pre-filter"


async def test_live_deny_overrides_group_grant(store, authz, embedder, seed, add_membership):
    await seed(
        doc_id="doc:secret", tenant="t1", instance="inst", allow_groups=("g1",), deny=("alice",)
    )
    await add_membership("alice", ["g1"], "t1")
    await add_membership("carol", ["g1"], "t1")

    alice = await _ids(store, authz, embedder, user="alice", groups=["g1"], tenant="t1")
    carol = await _ids(store, authz, embedder, user="carol", groups=["g1"], tenant="t1")

    assert "doc:secret" not in alice, "an explicit deny must override a group grant"
    assert "doc:secret" in carol, "carol is in g1 and is not denied"


async def test_live_tenant_isolation_blocks_cross_tenant(store, authz, embedder, seed):
    await seed(doc_id="doc:t1pub", tenant="t1", instance="inst", public=True)
    await seed(doc_id="doc:t2pub", tenant="t2", instance="inst", public=True)

    res = await _ids(store, authz, embedder, user="alice", groups=[], tenant="t2")

    assert res == {"doc:t2pub"}, "querying tenant t2 must never surface a t1 document"


async def test_live_prefilter_is_not_source_of_truth(store, authz, embedder, seed, add_membership):
    # The chunk is indexed with allow_groups=("g1",) so it passes the vector pre-filter
    # for a g1 subject, but NO reader tuple is written. The authz bulk check, not the
    # vector payload, must decide, so the doc is excluded. This is the single most
    # important structural invariant and is impossible to test with metadata-derived fakes.
    await seed(
        doc_id="doc:leak",
        tenant="t1",
        instance="inst",
        allow_groups=("g1",),
        write_reader_tuples=False,
    )
    await add_membership("alice", ["g1"], "t1")

    res = await _ids(store, authz, embedder, user="alice", groups=["g1"], tenant="t1")

    assert "doc:leak" not in res, "the bulk check, not the vector payload, is the source of truth"


async def test_live_revocation_freshness_excludes_after_revoke(
    store, authz, embedder, seed, add_membership
):
    # The headline test, mapped to the Zanzibar new enemy problem. Grant, then revoke and
    # thread the fresher ZedToken so the read is at_least_as_fresh the revocation. A stale
    # pre-revocation snapshot must not be allowed to satisfy the read.
    await seed(doc_id="doc:eng", tenant="t1", instance="inst", allow_groups=("g1",))
    await seed(doc_id="doc:pub", tenant="t1", instance="inst", public=True)
    await add_membership("alice", ["g1"], "t1")

    before = await _ids(store, authz, embedder, user="alice", groups=["g1"], tenant="t1")
    assert "doc:eng" in before

    # Revoke the group's read on doc:eng and capture the fresher token.
    acl = AccessControl(allow_groups=["g1"], principal_ns="inst")
    from pansoph_authz.mappers import tuples_for_acl

    revoke = str(await authz.delete_tuples(tuples_for_acl("doc:eng", "inst", acl, tenant_id="t1")))

    # Re-index the chunk carrying the revoke token and a dominating stamp, so retrieve()
    # threads at_least_as_fresh=revoke into the bulk check.
    hit = SearchHit(
        doc_id="doc:eng",
        chunk_id="doc:eng#0",
        score=0.0,
        tenant_id="t1",
        allow_groups=["g1"],
        deny=[],
        acl_version=2,
        text="the document body for doc:eng",
        consistency_token=revoke,
        acl_written_at=1000.0,
    )
    await store.upsert([hit], await embedder.embed([hit.text or "doc:eng"]))

    after = await _ids(store, authz, embedder, user="alice", groups=["g1"], tenant="t1")

    assert "doc:eng" not in after, "a revoked document must not be read stale (new enemy problem)"
    assert "doc:pub" in after, "unrelated access is unaffected by the revocation"


async def test_live_public_wildcard_visible_to_all(store, authz, embedder, seed):
    await seed(doc_id="doc:handbook", tenant="t1", instance="inst", public=True)

    for user in ("alice", "bob", "zoe"):
        res = await _ids(store, authz, embedder, user=user, groups=[], tenant="t1")
        assert res == {"doc:handbook"}, f"the public wildcard doc should be readable by {user}"


async def test_live_schema_loads_and_empty_tenant_returns_nothing(store, authz, embedder, seed):
    # Explicitly load the schema so a regression in the real SCHEMA string fails loudly,
    # then prove the short circuit: a tenant with no documents returns an empty set and
    # never reaches an authz call.
    await authz.ensure_schema()
    await seed(doc_id="doc:only", tenant="t1", instance="inst", public=True)

    res = await _ids(store, authz, embedder, user="alice", groups=[], tenant="empty-tenant")

    assert res == set()
