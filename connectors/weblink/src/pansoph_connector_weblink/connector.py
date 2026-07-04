"""The weblink connector: ingest any directly-fetchable public URL.

No credentials, no auth bypass: it fetches only openly reachable URLs. Each configured
URL becomes one document, with the ACL set from config (public by default). Every fetch
carries a descriptive User-Agent that identifies pansoph and gives a contact URL, which
many sites now require (see DEFAULT_USER_AGENT).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
from pansoph_connector_sdk import (
    ChangeEvent,
    ConfigField,
    ConnectorMeta,
    ConnectorSpec,
    FieldKind,
    ValidationResult,
)
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc

# A polite, honest User-Agent. Wikimedia and a growing number of sites enforce a
# User-Agent policy and return 403 for generic or unidentified bot agents (and for
# browser-spoof strings). The correct, non-bypass answer (pansoph CLAUDE.md section 5:
# identify yourself, no anti bot) is a descriptive agent that names the software and
# gives a contact URL. Operators should override user_agent with their own contact, per
# https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy
DEFAULT_USER_AGENT = "pansoph/0.1 (+https://github.com/autoargos/pansoph)"

# A normal browser Accept. Some origins 406/403 a request that omits it.
_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


class WeblinkConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.instance_id = instance_id
        self._urls: list[str] = list(cfg.get("urls", []))
        self._public: bool = bool(cfg.get("public", True))
        self._allow_groups: list[str] = list(cfg.get("allow_groups", []))
        # Operators can supply their own contact string; fall back to the honest default.
        self._user_agent: str = str(cfg.get("user_agent") or DEFAULT_USER_AGENT)
        self.meta = ConnectorMeta(
            name="weblink",
            auth=AuthScheme.none,
            capabilities=Capabilities(slim_listing=True),
        )

    @classmethod
    def spec(cls) -> ConnectorSpec:
        return ConnectorSpec(
            name="weblink",
            title="Public web links",
            description="Ingest public web pages over HTTPS. No credentials needed.",
            fields=(
                ConfigField(
                    key="urls",
                    label="URLs",
                    kind=FieldKind.LIST,
                    help="Absolute http(s) URLs to ingest, one per line.",
                ),
                ConfigField(
                    key="public",
                    label="Public to the whole tenant",
                    kind=FieldKind.BOOL,
                    required=False,
                    help="Expose ingested pages to everyone in the tenant.",
                ),
                ConfigField(
                    key="allow_groups",
                    label="Allowed groups",
                    kind=FieldKind.LIST,
                    required=False,
                    help="Groups allowed to read these pages.",
                ),
                ConfigField(
                    key="user_agent",
                    label="User-Agent",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder=DEFAULT_USER_AGENT,
                    help=(
                        "Sent on every fetch. Some sites (Wikipedia) reject generic bot "
                        "agents with 403; set a descriptive agent with your contact URL."
                    ),
                ),
            ),
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
        headers = {"user-agent": self._user_agent, "accept": _ACCEPT}
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True, headers=headers
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
