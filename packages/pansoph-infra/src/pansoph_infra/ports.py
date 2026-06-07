"""ObjectStore and SecretStore ports."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        """Store bytes at key."""
        ...

    async def get(self, key: str) -> bytes | None:
        """Return the bytes at key, or None if absent."""
        ...

    async def delete(self, key: str) -> None:
        """Delete the object at key. Idempotent."""
        ...


@runtime_checkable
class SecretStore(Protocol):
    async def set_secret(self, tenant_id: str, name: str, value: bytes) -> None:
        """Seal and store a secret for a tenant."""
        ...

    async def get_secret(self, tenant_id: str, name: str) -> bytes | None:
        """Return the unsealed secret, or None if absent."""
        ...

    async def delete_secret(self, tenant_id: str, name: str) -> None:
        """Delete a secret. Idempotent."""
        ...
