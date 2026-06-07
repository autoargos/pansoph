"""Envelope encryption secret store: round-trip, isolation, tamper detection."""

from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag
from pansoph_infra.adapters.envelope import EnvelopeSecretStore


async def test_round_trip() -> None:
    s = EnvelopeSecretStore(b"0" * 32)
    await s.set_secret("t1", "gh_token", b"super-secret")
    assert await s.get_secret("t1", "gh_token") == b"super-secret"


async def test_ciphertext_is_not_plaintext() -> None:
    backend: dict[str, bytes] = {}
    s = EnvelopeSecretStore(b"0" * 32, backend)
    await s.set_secret("t1", "n", b"plaintext-value")
    stored = backend["t1/n"]
    assert b"plaintext-value" not in stored


async def test_missing_returns_none_and_delete_is_idempotent() -> None:
    s = EnvelopeSecretStore(b"0" * 32)
    assert await s.get_secret("t1", "absent") is None
    await s.delete_secret("t1", "absent")  # no error


async def test_tenant_isolation_other_tenant_cannot_decrypt() -> None:
    backend: dict[str, bytes] = {}
    s = EnvelopeSecretStore(b"0" * 32, backend)
    await s.set_secret("t1", "n", b"v1")
    # Move t1's sealed blob under t2's reference. The per-tenant key and AAD differ,
    # so t2 cannot decrypt it.
    backend["t2/n"] = backend["t1/n"]
    with pytest.raises(InvalidTag):
        await s.get_secret("t2", "n")


async def test_tampering_is_detected() -> None:
    backend: dict[str, bytes] = {}
    s = EnvelopeSecretStore(b"0" * 32, backend)
    await s.set_secret("t1", "n", b"v")
    blob = bytearray(backend["t1/n"])
    blob[-1] ^= 0x01
    backend["t1/n"] = bytes(blob)
    with pytest.raises(InvalidTag):
        await s.get_secret("t1", "n")


def test_short_root_key_rejected() -> None:
    with pytest.raises(ValueError):
        EnvelopeSecretStore(b"tooshort")
