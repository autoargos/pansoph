"""The weblink connector: ingest any directly-fetchable public URL.

No credentials, no auth bypass: it fetches only openly reachable URLs. Each configured
URL becomes one document, with the ACL set from config (public by default).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
from pansoph_connector_sdk import ChangeEvent, ConnectorMeta, ValidationResult
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc


class WeblinkConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.instance_id = instance_id
        self._urls: list[str] = list(cfg.get("urls", []))
        self._public: bool = bool(cfg.get("public", True))
        self._allow_groups: list[str] = list(cfg.get("allow_groups", []))
        self.meta = ConnectorMeta(
            name="weblink",
            auth=AuthScheme.none,
            capabilities=Capabilities(slim_listing=True),
        )

    def _acl(self) -> AccessControl:
        return AccessControl(
            public=self._public, allow_groups=self._allow_groups, principal_ns=self.instance_id
        )

    def validate(self, config: dict[str, Any]) -> ValidationResult:
        if not config.get("urls"):
            return ValidationResult(ok=False, error="missing urls")
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        for url in self._urls:
            yield DocRef(source_doc_id=url, instance_id=self.instance_id), ""

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True, headers={"user-agent": "pansoph-weblink"}
        ) as client:
            resp = await client.get(ref.source_doc_id)
            resp.raise_for_status()
            mime = resp.headers.get("content-type", "text/plain").split(";")[0]
            raw = RawDoc(
                source_doc_id=ref.source_doc_id,
                instance_id=self.instance_id,
                mime=mime,
                text=resp.text,
                title=ref.source_doc_id.rsplit("/", 1)[-1] or ref.source_doc_id,
                source_url=ref.source_doc_id,
            )
            return raw, self._acl()

    async def list_acls(self, ref: DocRef) -> AccessControl:
        return self._acl()

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        return []
