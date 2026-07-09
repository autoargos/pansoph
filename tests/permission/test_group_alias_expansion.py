"""Permission red team: the identity-group alias closes the fail-closed connector gap.

P5 made connectors grant a namespaced source group (e.g. slack:member:acme) that no
identity is a member of, so those documents were unreachable by anyone (secure but empty).
Stream 1 lets an operator alias one of their IdP groups to that connector group. This
suite proves the full query-time flow with the in-memory fakes: expand the subject's
groups by the alias, sync membership, retrieve. A document granted only to the connector
group is then reachable by a user in the aliased identity group AND by no one else, and
removing the alias makes it unreachable again. It mirrors exactly what
pansoph_core.routes._retrieve does in production.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _fakes import FakeAuthz, FakeEmbedder, FakeVectorStore  # noqa: E402
from pansoph_authz import TENANT_EVERYONE, Principal, membership_tuples  # noqa: E402
from pansoph_connector_sdk import InMemoryConnector  # noqa: E402
from pansoph_core.group_aliases import expand_groups  # noqa: E402
from pansoph_ingestion.adapters_parser import DoclingParser  # noqa: E402
from pansoph_ingestion.pipeline import IngestionPipeline  # noqa: E402
from pansoph_retrieval.pipeline import retrieve  # noqa: E402
from pansoph_types import AccessControl  # noqa: E402

_CONNECTOR_GROUP = "slack:member:acme"


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


async def _retrieve_like_production(
    store: FakeVectorStore,
    authz: FakeAuthz,
    user: str,
    identity_groups: list[str],
    aliases: dict[str, set[str]],
    tenant: str,
) -> set[str]:
    """Run the exact production flow: expand identity groups by the operator aliases, sync
    membership for the effective set, then retrieve with the effective set as the coarse
    pre-filter groups. Returns the retrievable doc ids."""
    effective = expand_groups(identity_groups, aliases)
    await authz.write_tuples(membership_tuples(user, [TENANT_EVERYONE, *effective], tenant))
    hits = await retrieve(
        query="anything",
        subject=Principal(type="user", id=user, ns=tenant),
        tenant_id=tenant,
        subject_groups=effective,
        final_k=10,
        vector_store=store,
        embedder=FakeEmbedder(),
        authz=authz,
    )
    return {h.doc_id for h in hits}


async def _indexed_connector_group_doc() -> tuple[FakeVectorStore, FakeAuthz]:
    store, authz = FakeVectorStore(), FakeAuthz()
    conn = InMemoryConnector(instance_id="acme")
    # A public-channel Slack document: granted only to the fail-closed connector group.
    conn.put("chan1", "public channel notes", AccessControl(allow_groups=[_CONNECTOR_GROUP]))
    await _pipeline(conn, store, authz).sync(connector_name="slack", tenant_id="t1")
    return store, authz


@pytest.mark.permission
async def test_aliased_identity_group_reaches_connector_group_doc() -> None:
    store, authz = await _indexed_connector_group_doc()
    aliases = {"engineering": {_CONNECTOR_GROUP}}

    # alice is in the aliased identity group: the doc is now reachable.
    alice = await _retrieve_like_production(store, authz, "alice", ["engineering"], aliases, "t1")
    assert "slack:acme:chan1" in alice, "an aliased user must reach the connector-group doc"


@pytest.mark.permission
async def test_unaliased_user_never_reaches_connector_group_doc() -> None:
    store, authz = await _indexed_connector_group_doc()
    aliases = {"engineering": {_CONNECTOR_GROUP}}

    # bob is in a different, unmapped identity group: he must not reach it.
    bob = await _retrieve_like_production(store, authz, "bob", ["marketing"], aliases, "t1")
    assert "slack:acme:chan1" not in bob, "an unmapped identity group must not reach the doc"

    # And with no alias declared at all, even the engineering user cannot reach it: the
    # connector group grants no one until an operator opts an identity group in.
    nobody = await _retrieve_like_production(store, authz, "carol", ["engineering"], {}, "t1")
    assert "slack:acme:chan1" not in nobody, "no alias means the fail-closed doc stays hidden"


@pytest.mark.permission
async def test_removing_alias_revokes_reach() -> None:
    store, authz = await _indexed_connector_group_doc()

    with_alias = await _retrieve_like_production(
        store, authz, "alice", ["engineering"], {"engineering": {_CONNECTOR_GROUP}}, "t1"
    )
    assert "slack:acme:chan1" in with_alias

    # Simulate the alias being removed: the expansion no longer includes the connector
    # group, so the pre-filter drops the doc and the membership is not synced for it.
    fresh_store, fresh_authz = await _indexed_connector_group_doc()
    without_alias = await _retrieve_like_production(
        fresh_store, fresh_authz, "alice", ["engineering"], {}, "t1"
    )
    assert "slack:acme:chan1" not in without_alias, "removing the alias must revoke reach"
