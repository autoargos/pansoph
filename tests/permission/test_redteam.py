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
        subject=Principal(type="user", id=user),
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
async def test_revocation_is_reflected() -> None:
    store, authz = await _scenario()
    before = await _ids(store, authz, "alice", ["g1"], "t1")
    assert "memory:mem:eng" in before

    await authz.delete_tuples(
        [
            Tuple(
                subject=Principal(type="group", id="g1", ns="mem"),
                relation="reader",
                object_type="document",
                object_id="memory:mem:eng",
            )
        ]
    )
    after = await _ids(store, authz, "alice", ["g1"], "t1")
    assert "memory:mem:eng" not in after, "revoked document must disappear from results"
    assert "memory:mem:pub" in after, "unrelated access is unaffected"
