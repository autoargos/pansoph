"""Secret sealing: a connector's secret fields are sealed into the store and replaced by
handles in the persisted config; resolve restores plaintext for one run. Uses the in-memory
envelope store, no Postgres. No em dashes anywhere.
"""

from __future__ import annotations

from pansoph_core.secrets import resolve_config, seal_config, secret_handle
from pansoph_infra.adapters.envelope import EnvelopeSecretStore

_ROOT = b"0123456789abcdef"  # 16 bytes, the envelope store minimum


async def test_seal_replaces_secret_with_handle_and_resolve_restores() -> None:
    store = EnvelopeSecretStore(root_key=_ROOT)
    config = {"owner": "cyproto", "token": "ntn_supersecret", "public": True}

    sealed = await seal_config(store, "default", "inst1", config, secret_keys=("token",))

    # Non-secret fields untouched; the secret is replaced by a handle, not plaintext.
    assert sealed["owner"] == "cyproto"
    assert sealed["public"] is True
    assert secret_handle(sealed["token"]) == "inst1/token"
    assert "ntn_supersecret" not in str(sealed)
    # The plaintext lives only in the (sealing) store.
    assert await store.get_secret("default", "inst1/token") == b"ntn_supersecret"

    # Resolve restores plaintext for a run.
    resolved = await resolve_config(store, "default", sealed)
    assert resolved["token"] == "ntn_supersecret"
    assert resolved["owner"] == "cyproto"


async def test_resolve_is_noop_without_handles() -> None:
    store = EnvelopeSecretStore(root_key=_ROOT)
    config = {"urls": ["https://example.com"], "public": False}
    assert await resolve_config(store, "default", config) == config


async def test_seal_drops_empty_secret_and_is_idempotent() -> None:
    store = EnvelopeSecretStore(root_key=_ROOT)
    # An empty secret is dropped rather than sealed.
    sealed = await seal_config(store, "default", "i", {"token": ""}, ("token",))
    assert "token" not in sealed
    # An already-sealed handle is left as is (a config edit that did not change the secret).
    again = await seal_config(store, "default", "i", {"token": {"_secret": "i/token"}}, ("token",))
    assert again["token"] == {"_secret": "i/token"}
