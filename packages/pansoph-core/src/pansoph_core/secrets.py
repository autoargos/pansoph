"""Secret sealing for connector instances.

On instance create, the secret-kind config fields a connector declares in its ConnectorSpec
are sealed into the SecretStore and replaced in the stored config by a handle
(`{"_secret": name}`). On sync the runtime resolves the handles back to plaintext for that
one run. So the connector_instances config column never holds a plaintext credential. This
is the design pattern every connector follows, third-party included.

The SecretStore is pluggable (envelope by default, OpenBao for production). The envelope
store is given a Postgres-backed map here so sealed secrets persist across restarts. Secret
operations happen at instance create (an admin action) and at sync start (once per run, a
handful of secrets), never on the retrieval hot path, so the backend's synchronous Postgres
round trips are acceptable.

No em dashes anywhere.
"""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from typing import Any

_SECRETS_DDL = """
CREATE TABLE IF NOT EXISTS connector_secrets (
    ref  text PRIMARY KEY,
    blob bytea NOT NULL
);
"""


class PostgresSecretBackend(MutableMapping[str, bytes]):
    """A durable MutableMapping over a Postgres table, the backend for the envelope secret
    store. Sync psycopg by design: it is called off the hot path, and keeping it sync lets
    it satisfy the sync MutableMapping protocol the envelope store expects.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._ready = False

    def _conn(self) -> Any:
        import psycopg

        conn = psycopg.connect(self._dsn, autocommit=True)
        if not self._ready:
            conn.execute(_SECRETS_DDL)
            self._ready = True
        return conn

    def __getitem__(self, key: str) -> bytes:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT blob FROM connector_secrets WHERE ref = %s", (key,)
            ).fetchone()
        if row is None:
            raise KeyError(key)
        return bytes(row[0])

    def __setitem__(self, key: str, value: bytes) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO connector_secrets (ref, blob) VALUES (%s, %s) "
                "ON CONFLICT (ref) DO UPDATE SET blob = EXCLUDED.blob",
                (key, value),
            )

    def __delitem__(self, key: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM connector_secrets WHERE ref = %s", (key,))

    def __iter__(self) -> Iterator[str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT ref FROM connector_secrets").fetchall()
        return iter([r[0] for r in rows])

    def __len__(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT count(*) FROM connector_secrets").fetchone()
        return int(row[0]) if row else 0


def secret_handle(value: Any) -> str | None:
    """Return the store name a `{"_secret": name}` handle points at, or None if `value` is
    not a handle. Used to detect already-sealed fields on resolve and re-seal."""
    if isinstance(value, dict):
        name = value.get("_secret")
        if isinstance(name, str):
            return name
    return None


async def seal_config(
    store: Any,
    tenant_id: str,
    instance_id: str,
    config: dict[str, Any],
    secret_keys: tuple[str, ...],
) -> dict[str, Any]:
    """Seal the named secret fields into `store`, replacing each with a handle.

    Returns the config to persist: handles for secrets, plaintext for everything else. An
    empty or already-sealed secret field is left as is (an empty one is dropped), so the
    call is idempotent across re-creates and edits.
    """
    out = dict(config)
    for key in secret_keys:
        if key not in out:
            continue
        if secret_handle(out[key]) is not None:
            continue  # already a handle (an edit that did not change the secret)
        value = out[key]
        if value in (None, ""):
            out.pop(key, None)
            continue
        name = f"{instance_id}/{key}"
        await store.set_secret(tenant_id, name, str(value).encode("utf-8"))
        out[key] = {"_secret": name}
    return out


async def resolve_config(store: Any, tenant_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """Resolve every `{"_secret": name}` handle in `config` back to plaintext for one run.

    A config with no handles (a legacy plaintext instance, or a connector with no secrets)
    is returned unchanged, so this is safe to call on every sync.
    """
    out = dict(config)
    for key, value in list(out.items()):
        name = secret_handle(value)
        if name is None:
            continue
        raw = await store.get_secret(tenant_id, name)
        out[key] = raw.decode("utf-8") if raw is not None else ""
    return out
