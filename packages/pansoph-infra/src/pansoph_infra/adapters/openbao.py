"""OpenBao secret store adapter. The opt-in for dynamic secrets and audit.

Talks to OpenBao's KV v2 API over HTTP (install pansoph-infra[openbao]). Secret
bytes are base64 encoded into the KV value. Per-tenant path prefix gives isolation.
Not Vault, which is now BUSL.
"""

from __future__ import annotations

import base64

import httpx


class OpenBaoSecretStore:
    def __init__(self, addr: str, token: str, mount: str = "secret") -> None:
        self._addr = addr.rstrip("/")
        self._token = token
        self._mount = mount

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._addr, headers={"X-Vault-Token": self._token}, timeout=15.0
        )

    def _path(self, tenant_id: str, name: str, kind: str) -> str:
        return f"/v1/{self._mount}/{kind}/{tenant_id}/{name}"

    async def set_secret(self, tenant_id: str, name: str, value: bytes) -> None:
        encoded = base64.b64encode(value).decode()
        async with self._client() as client:
            resp = await client.post(
                self._path(tenant_id, name, "data"), json={"data": {"value": encoded}}
            )
            resp.raise_for_status()

    async def get_secret(self, tenant_id: str, name: str) -> bytes | None:
        async with self._client() as client:
            resp = await client.get(self._path(tenant_id, name, "data"))
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            encoded = resp.json()["data"]["data"]["value"]
            return base64.b64decode(encoded)

    async def delete_secret(self, tenant_id: str, name: str) -> None:
        async with self._client() as client:
            await client.delete(self._path(tenant_id, name, "metadata"))
