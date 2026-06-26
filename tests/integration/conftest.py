"""Fixtures for the live enforcement integration suite.

These tests run the real `retrieve()` path against a real SpiceDB and a real pgvector,
so a SpiceDB schema bug, a ZedToken freshness bug, or a vector pre filter group leak
fails the build. The fast unit suite (`pytest -m "not integration"`) never touches this.

Bring up the engines with `deploy/compose.test.yaml` and point the suite at them:

    docker compose -f deploy/compose.test.yaml up -d
    export PANSOPH_SPICEDB_ENDPOINT=localhost:50051
    export PANSOPH_PG_DSN=postgresql://pansoph:pansoph@localhost:5433/pansoph
    uv sync --all-packages --group dev --group integration
    uv run pytest -m integration -q

When those env vars are absent (the default local run, and any machine without Docker),
every test in this directory skips cleanly with a clear reason, so the suite is never a
silent no op the way `pytest -m integration -q || true` was.

Per test isolation:
- SpiceDB: each test gets a unique preshared key, and `spicedb serve-testing` gives each
  distinct key its own ephemeral in memory datastore, so tests never share tuples.
- pgvector: the store table is truncated before each test.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from pansoph_authz.adapters.spicedb import SpiceDBAuthz
from pansoph_authz.mappers import membership_tuples, tuples_for_acl
from pansoph_retrieval.adapters_vector import PgVectorScaleStore
from pansoph_retrieval.ports import SearchHit
from pansoph_types import AccessControl

SPICEDB_ENDPOINT = os.environ.get("PANSOPH_SPICEDB_ENDPOINT")
PG_DSN = os.environ.get("PANSOPH_PG_DSN")
EMBED_DIM = 8


def _live_env_present() -> bool:
    return bool(SPICEDB_ENDPOINT and PG_DSN)


@pytest.fixture(autouse=True)
def _require_live_env() -> None:
    """Skip every test here unless both engines are configured. Keeps the suite honest
    on machines without Docker instead of pretending to pass."""
    if not _live_env_present():
        pytest.skip(
            "live integration env not set (PANSOPH_SPICEDB_ENDPOINT, PANSOPH_PG_DSN); "
            "see deploy/compose.test.yaml"
        )


class TinyEmbedder:
    """A deterministic, model free embedder. This suite proves enforcement, not
    retrieval quality, so a hash based vector is exactly right: stable, fast, no model
    server, and it keeps the dense pre filter exercising real pgvector distance."""

    model = "tiny-deterministic"
    dim = EMBED_DIM

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            out.append([byte / 255.0 for byte in digest[:EMBED_DIM]])
        return out


@pytest.fixture
def embedder() -> TinyEmbedder:
    return TinyEmbedder()


@pytest.fixture
async def store() -> AsyncIterator[PgVectorScaleStore]:
    """A real pgvector store, truncated before the test so rows never leak across tests."""
    assert PG_DSN is not None
    st = PgVectorScaleStore(PG_DSN, dim=EMBED_DIM)
    # Run the store's own idempotent DDL (extension, table, columns, indexes), then start
    # from an empty table. _connect returns the open connection after applying the DDL.
    ddl_conn = await st._connect()  # noqa: SLF001  test setup reuses the store's real DDL
    try:
        await ddl_conn.execute("TRUNCATE pansoph_chunks")
    finally:
        await ddl_conn.close()
    yield st


@pytest.fixture
def authz(request: pytest.FixtureRequest) -> SpiceDBAuthz:
    """A real SpiceDB client with a per test preshared key. `serve-testing` maps each
    distinct key to its own isolated, empty datastore, so tests are independent without
    any teardown."""
    assert SPICEDB_ENDPOINT is not None
    # A unique, SpiceDB safe key per test. serve-testing accepts any token as the
    # datastore selector, so a fresh key is a fresh datastore.
    key = "test_" + hashlib.sha1(request.node.name.encode()).hexdigest()[:16]  # noqa: S324
    return SpiceDBAuthz(SPICEDB_ENDPOINT, key)


# A seed helper: write the document's reader tuples and upsert its chunk into pgvector,
# exactly as the real ingestion path does, and return the write token.
SeedFn = Callable[..., Awaitable[str]]


@pytest.fixture
def seed(authz: SpiceDBAuthz, store: PgVectorScaleStore, embedder: TinyEmbedder) -> SeedFn:
    counter = {"n": 0}

    async def _seed(
        *,
        doc_id: str,
        tenant: str,
        instance: str,
        public: bool = False,
        allow_groups: tuple[str, ...] = (),
        allow_users: tuple[str, ...] = (),
        deny: tuple[str, ...] = (),
        write_reader_tuples: bool = True,
    ) -> str:
        """Grant a document and index its chunk. When `write_reader_tuples` is False the
        chunk is indexed but NO authz tuple is written, which is how the
        "pre filter is not the source of truth" test seeds a doc that passes the vector
        pre filter yet must be denied by the bulk check."""
        acl = AccessControl(
            public=public,
            allow_groups=list(allow_groups),
            allow_users=list(allow_users),
            deny=list(deny),
            principal_ns=instance,
        )
        token = ""
        if write_reader_tuples:
            token = str(
                await authz.write_tuples(tuples_for_acl(doc_id, instance, acl, tenant_id=tenant))
            )
        counter["n"] += 1
        hit = SearchHit(
            doc_id=doc_id,
            chunk_id=f"{doc_id}#0",
            score=0.0,
            tenant_id=tenant,
            allow_groups=list(allow_groups),
            deny=list(deny),
            acl_version=1,
            text=f"the document body for {doc_id}",
            consistency_token=token,
            acl_written_at=float(counter["n"]),
        )
        vectors = await embedder.embed([hit.text or doc_id])
        await store.upsert([hit], vectors)
        return token

    return _seed


@pytest.fixture
def add_membership(authz: SpiceDBAuthz) -> Callable[[str, list[str], str], Awaitable[Any]]:
    """Write a subject's group memberships into the authz graph, as the Identity port
    does on login. Required because retrieve() does not write memberships itself; the
    engine must already know them to resolve a group granted document for the user."""

    async def _add(user: str, groups: list[str], tenant: str) -> Any:
        return await authz.write_tuples(membership_tuples(user, groups, tenant))

    return _add
