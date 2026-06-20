"""Compile a declarative manifest into a class satisfying the Connector protocol.

The compiled connector only describes how to talk to the source for one page or one
document. The durable runtime still owns the retry, backoff, and cursor checkpoint
loop. A single-stream manifest is supported in this first cut; multi-stream support
extends the same factory by iterating streams.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
from pansoph_types import AccessControl, AuthScheme, DocRef, RawDoc

from ..contract import ChangeEvent, ConnectorMeta, ValidationResult
from .components import build_acl, extract, extract_one
from .schema import ConnectorManifest


def compile_manifest(manifest: ConnectorManifest) -> type:
    """Return a Connector class for this manifest. Validate-on-construct already
    happened when the ConnectorManifest model was built."""
    if len(manifest.streams) != 1:
        raise NotImplementedError("the first compiler cut supports a single stream")
    stream = manifest.streams[0]

    class _Compiled:
        def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
            self.instance_id = instance_id
            cfg = config or {}
            self._token: str = cfg.get("token", "")
            self._base_url: str = cfg.get("base_url", manifest.base_url or "")
            self.meta = ConnectorMeta(
                name=manifest.connector,
                auth=manifest.auth.type,
                capabilities=manifest.capabilities,
            )

        def _client(self) -> httpx.AsyncClient:
            headers: dict[str, str] = {}
            if self._token and manifest.auth.type != AuthScheme.none:
                headers["Authorization"] = f"Bearer {self._token}"
            return httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=30.0)

        def validate(self, config: dict[str, Any]) -> ValidationResult:
            return ValidationResult(ok=True)

        async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
            async with self._client() as client:
                resp = await client.get(stream.list.path)
                resp.raise_for_status()
                payload = resp.json()
                records = extract(payload, stream.list.records) if stream.list.records else payload
                for rec in records:
                    doc_id = str(rec.get(stream.list.id_field))
                    yield DocRef(source_doc_id=doc_id, instance_id=self.instance_id), doc_id

        async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
            async with self._client() as client:
                path = stream.fetch.path.replace("{id}", ref.source_doc_id)
                resp = await client.get(path)
                resp.raise_for_status()
                payload = resp.json()
                text = extract_one(payload, stream.fetch.text, default="")
                raw = RawDoc(
                    source_doc_id=ref.source_doc_id,
                    instance_id=self.instance_id,
                    mime="text/plain",
                    text=str(text),
                )
                return raw, self._acl(payload)

        async def list_acls(self, ref: DocRef) -> AccessControl:
            async with self._client() as client:
                path = stream.fetch.path.replace("{id}", ref.source_doc_id)
                resp = await client.get(path)
                resp.raise_for_status()
                return self._acl(resp.json())

        def _acl(self, payload: Any) -> AccessControl:
            if stream.acl is None:
                return AccessControl(public=True, principal_ns=self.instance_id)
            return build_acl(payload, stream.acl, principal_ns=self.instance_id)

        async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
            return []

    return _Compiled
