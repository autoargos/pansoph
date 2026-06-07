"""Envelope encryption secret store.

Per-tenant data keys are derived from a single root key with HKDF, and each secret
is sealed with AES-GCM under its tenant's data key. This gives encryption at rest
and tenant isolation with no external service. The sealed bytes are kept in a
backing map, which is in-memory here and a Postgres table in agora.

The root key never appears in source control. Supply it as raw bytes (for example
from an age key or a KMS).
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_NONCE_LEN = 12
_KEY_LEN = 32


class EnvelopeSecretStore:
    def __init__(self, root_key: bytes, backend: MutableMapping[str, bytes] | None = None) -> None:
        if len(root_key) < 16:
            raise ValueError("root key must be at least 16 bytes")
        self._root = root_key
        self._store: MutableMapping[str, bytes] = {} if backend is None else backend

    def _dek(self, tenant_id: str) -> bytes:
        """Derive a tenant data key from the root key. Deterministic per tenant."""
        hkdf = HKDF(
            algorithm=SHA256(), length=_KEY_LEN, salt=tenant_id.encode(), info=b"pansoph-dek"
        )
        return hkdf.derive(self._root)

    @staticmethod
    def _ref(tenant_id: str, name: str) -> str:
        return f"{tenant_id}/{name}"

    async def set_secret(self, tenant_id: str, name: str, value: bytes) -> None:
        nonce = os.urandom(_NONCE_LEN)
        sealed = AESGCM(self._dek(tenant_id)).encrypt(nonce, value, tenant_id.encode())
        self._store[self._ref(tenant_id, name)] = nonce + sealed

    async def get_secret(self, tenant_id: str, name: str) -> bytes | None:
        blob = self._store.get(self._ref(tenant_id, name))
        if blob is None:
            return None
        nonce, sealed = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
        return AESGCM(self._dek(tenant_id)).decrypt(nonce, sealed, tenant_id.encode())

    async def delete_secret(self, tenant_id: str, name: str) -> None:
        self._store.pop(self._ref(tenant_id, name), None)
