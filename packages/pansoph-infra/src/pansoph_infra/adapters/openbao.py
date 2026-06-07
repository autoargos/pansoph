"""OpenBao secret store adapter. The opt-in for dynamic secrets and audit.

Talks to OpenBao's KV API over HTTP (install pansoph-infra[openbao]). Wiring lands
with agora's configuration layer; this fixes the adapter shape. Not Vault, which is
now BUSL.
"""

from __future__ import annotations


class OpenBaoSecretStore:
    def __init__(self, addr: str, token: str, mount: str = "pansoph") -> None:
        self._addr = addr
        self._token = token
        self._mount = mount

    async def set_secret(self, tenant_id: str, name: str, value: bytes) -> None:
        raise NotImplementedError("OpenBao set lands with the agora configuration layer")

    async def get_secret(self, tenant_id: str, name: str) -> bytes | None:
        raise NotImplementedError("OpenBao get lands with the agora configuration layer")

    async def delete_secret(self, tenant_id: str, name: str) -> None:
        raise NotImplementedError("OpenBao delete lands with the agora configuration layer")
