"""Short-lived, single-use OAuth `state` records.

The `state` value carried through the authorization-code flow is the CSRF guard:
the callback is trusted only if its `state` matches a record we created when we
built the authorize URL. Each record also carries the flow's target (provider,
connector, instance, tenant) and, when PKCE is used, the code verifier, so the
callback has everything it needs without trusting any query parameter but `state`
and `code`.

Storage choice (documented per the task): Postgres via the existing dsn, not a
signed token. Rationale:
  - No new dependency. itsdangerous is not in the tree, and adding a dependency
    needs an ADR (CLAUDE.md). Postgres is already the metadata store.
  - Single use is trivial and reliable with a DELETE-on-read, which a stateless
    signed token cannot guarantee (a signed token is replayable until it expires).
  - The PKCE code_verifier is a secret-ish value that should not travel in a
    cookie or a URL; keeping it server side is cleaner.
Expiry is enforced both by a stored `expires_at` (checked on consume) and by an
opportunistic sweep of stale rows, so an abandoned flow cannot accumulate.

The store is defined against a small `StateStore` protocol so the hermetic tests
use an in-memory backend and production uses Postgres, with identical semantics.

No em dashes anywhere.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

# A generous default: consent screens can take a while, but a state older than this
# is treated as abandoned and rejected.
_DEFAULT_TTL_SECONDS = 600


@dataclass(frozen=True)
class OAuthState:
    """The server-side record a `state` value points at.

    `code_verifier` is present only for PKCE providers. `redirect_uri` is stored so
    the exact value sent to the provider on authorize is replayed on exchange (some
    providers require an exact match).
    """

    provider: str
    connector: str
    instance: str
    tenant: str
    redirect_uri: str
    code_verifier: str | None = None


def new_state_token() -> str:
    """Return a fresh unguessable state token (the CSRF nonce)."""
    return secrets.token_urlsafe(32)


@runtime_checkable
class StateStore(Protocol):
    """Persist and single-use-consume OAuth state records.

    `put` stores a record under a freshly generated token and returns the token.
    `consume` returns the record for a token exactly once, deleting it, and returns
    None if the token is unknown or expired. Single use is the security contract.
    """

    async def put(self, state: OAuthState, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> str: ...

    async def consume(self, token: str) -> OAuthState | None: ...


class InMemoryStateStore:
    """An in-process StateStore for tests and single-worker local runs.

    Not durable and not shared across processes, so it is unsuitable for a
    multi-worker deployment (use the Postgres store there). Enforces single use and
    expiry with the same semantics as the Postgres store so tests are meaningful.
    """

    def __init__(self) -> None:
        self._rows: dict[str, tuple[OAuthState, datetime]] = {}

    async def put(self, state: OAuthState, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> str:
        token = new_state_token()
        self._rows[token] = (state, datetime.now(UTC) + timedelta(seconds=ttl_seconds))
        return token

    async def consume(self, token: str) -> OAuthState | None:
        self._sweep()
        row = self._rows.pop(token, None)
        if row is None:
            return None
        state, expires_at = row
        if datetime.now(UTC) >= expires_at:
            return None
        return state

    def _sweep(self) -> None:
        now = datetime.now(UTC)
        for token in [t for t, (_, exp) in self._rows.items() if now >= exp]:
            self._rows.pop(token, None)


_STATE_DDL = """
CREATE TABLE IF NOT EXISTS oauth_states (
    token       text PRIMARY KEY,
    provider    text NOT NULL,
    connector   text NOT NULL,
    instance    text NOT NULL,
    tenant      text NOT NULL,
    redirect_uri text NOT NULL,
    code_verifier text,
    expires_at  timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS oauth_states_expiry ON oauth_states (expires_at);
"""


class PostgresStateStore:
    """A durable StateStore over a Postgres table, keyed by the state token.

    Single use is enforced with a DELETE ... RETURNING, so a replayed callback
    finds nothing. Expiry is enforced by the returned `expires_at` check and by a
    best-effort sweep of stale rows on each consume, so an abandoned flow is
    cleaned up without a separate cron.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._ready = False

    async def _conn(self) -> Any:
        import psycopg

        conn = await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)
        if not self._ready:
            await conn.execute(_STATE_DDL)
            self._ready = True
        return conn

    async def put(self, state: OAuthState, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> str:
        token = new_state_token()
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        conn = await self._conn()
        try:
            await conn.execute(
                "INSERT INTO oauth_states (token, provider, connector, instance, tenant, "
                "redirect_uri, code_verifier, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    token,
                    state.provider,
                    state.connector,
                    state.instance,
                    state.tenant,
                    state.redirect_uri,
                    state.code_verifier,
                    expires_at,
                ),
            )
        finally:
            await conn.close()
        return token

    async def consume(self, token: str) -> OAuthState | None:
        conn = await self._conn()
        try:
            # Best-effort sweep of expired rows so abandoned flows do not accumulate.
            await conn.execute("DELETE FROM oauth_states WHERE expires_at < now()")
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM oauth_states WHERE token = %s RETURNING provider, connector, "
                    "instance, tenant, redirect_uri, code_verifier, expires_at",
                    (token,),
                )
                row = await cur.fetchone()
        finally:
            await conn.close()
        if row is None:
            return None
        expires_at = row[6]
        if expires_at is not None and datetime.now(UTC) >= expires_at:
            return None
        return OAuthState(
            provider=row[0],
            connector=row[1],
            instance=row[2],
            tenant=row[3],
            redirect_uri=row[4],
            code_verifier=row[5],
        )
