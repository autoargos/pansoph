"""The notion connector.

Implemented against the official Notion API. Verified against the official docs:

  API docs:    https://developers.notion.com/reference/intro (overview, auth)
               https://developers.notion.com/reference/post-search (list pages)
               https://developers.notion.com/reference/get-block-children (page content)
               https://developers.notion.com/reference/rich-text (plain_text extraction)
               https://developers.notion.com/reference/request-limits (pagination, limits)
               https://developers.notion.com/reference/versioning (Notion-Version header)
  API version: Notion-Version 2025-09-03 (sent as a mandatory header on every request).
  Auth:        bearer token. Config "token" carries an internal integration token
               (or an OAuth access token). Sent as "Authorization: Bearer <token>".
               The mandatory "Notion-Version" header MUST accompany every call or
               the API rejects it.
  Base URL:    https://api.notion.com/v1, overridable via config "base_url".
  Pagination:  cursor based. POST /v1/search and GET block children both return
               "has_more" and "next_cursor". Pass "start_cursor" to continue.
               list_changes follows this pagination internally to the end, so one
               call yields a complete listing.
  Incremental: not yet. The first cut performs a full listing each run by following
               Notion search pagination; incremental is a follow-up. capabilities
               .incremental is therefore False.

  ACL CAVEAT (read this, it is a deliberate product decision, not a stub):
  ===================================================================================
  NOTION EXPOSES NO API THAT RETURNS WHO CAN SEE A PAGE. There is no per document
  permission endpoint. We therefore DO NOT and CANNOT mirror real Notion sharing.
  Access in pansoph is whatever the OPERATOR CONFIGURED for this instance, applied
  uniformly to every page this instance ingests:
      config "public"       (bool, default False)
      config "allow_groups" (list, default [])
      config "allow_users"  (list, default [])
  list_acls returns exactly that configured AccessControl, namespaced by instance.
  Because there is no real permission data, capabilities.permission_sync is False.
  An operator who points this connector at content must understand: NOTION SHARING
  IS NOT MIRRORED. If you configure public=True you publish every ingested page to
  every user in this instance, regardless of who could see it in Notion. Scope the
  instance's integration to only the pages you intend to expose, and set the ACL to
  match the audience you intend.
  ===================================================================================

  Verified on: 2026-06-20.

Credential scope note: every configured instance carries its own integration token
and its own ACL namespace (principal_ns = instance_id). Two instances never collide.
See pansoph CLAUDE.md section 6.

The runtime owns retries, backoff, and resumable sync, so a method only describes
how to talk to Notion for one page or one document.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
from pansoph_connector_sdk import ChangeEvent, ConnectorMeta, ValidationResult
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc

_API_VERSION = "2025-09-03"
_PAGE_SIZE = 100


class NotionConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        self.instance_id = instance_id
        cfg = config or {}
        self._token: str = cfg.get("token", "")
        self._base_url: str = cfg.get("base_url", "https://api.notion.com")
        # Operator configured default ACL. Notion has no per page permission API, so
        # this is the ONLY access control the connector can apply. See module header.
        self._public: bool = bool(cfg.get("public", False))
        self._allow_groups: list[str] = list(cfg.get("allow_groups", []))
        self._allow_users: list[str] = list(cfg.get("allow_users", []))
        self.meta = ConnectorMeta(
            name="notion",
            auth=AuthScheme.api_token,
            capabilities=Capabilities(
                # permission_sync is False on purpose: Notion exposes no per document
                # permission API, so there is nothing to sync. See module header.
                # incremental is False on purpose: the first cut performs a full
                # listing each run by following Notion search pagination; incremental
                # is a follow-up. See list_changes.
                incremental=False,
                permission_sync=False,
                deletions=False,
                slim_listing=True,
            ),
        )

    # --- contract ---
    def validate(self, config: dict[str, Any]) -> ValidationResult:
        if not config.get("token"):
            return ValidationResult(ok=False, error="missing config: token")
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        """List every page by following Notion search pagination to the end.

        First cut performs a full listing each run by following Notion search
        pagination; incremental is a follow-up. We loop the Notion cursor
        internally so one call yields a complete listing rather than relying on
        the runtime to re drive with start_cursor.
        """
        async with self._client() as client:
            body: dict[str, Any] = {
                "filter": {"property": "object", "value": "page"},
                "page_size": _PAGE_SIZE,
            }
            if cursor:
                body["start_cursor"] = cursor
            while True:
                resp = await client.post("/v1/search", json=body)
                resp.raise_for_status()
                payload = resp.json()
                for page in payload.get("results", []):
                    page_id = page.get("id")
                    if page_id:
                        yield self._ref(page_id), ""
                if not payload.get("has_more"):
                    break
                body["start_cursor"] = payload.get("next_cursor")

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        async with self._client() as client:
            resp = await client.get(f"/v1/blocks/{ref.source_doc_id}/children")
            resp.raise_for_status()
            text = self._extract_text(resp.json().get("results", []))
            raw = RawDoc(
                source_doc_id=ref.source_doc_id,
                instance_id=self.instance_id,
                mime="text/plain",
                text=text,
                title=ref.source_doc_id,
            )
            return raw, self._acl()

    async def list_acls(self, ref: DocRef) -> AccessControl:
        # Notion exposes no per page permission API. Return the operator configured
        # default ACL for this instance. See module header for the loud caveat.
        return self._acl()

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        return []

    # --- helpers ---
    def _client(self) -> httpx.AsyncClient:
        headers = {
            "Notion-Version": _API_VERSION,
            "Authorization": f"Bearer {self._token}",
        }
        return httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=30.0)

    def _ref(self, page_id: str) -> DocRef:
        return DocRef(source_doc_id=page_id, instance_id=self.instance_id)

    def _acl(self) -> AccessControl:
        return AccessControl(
            public=self._public,
            allow_groups=list(self._allow_groups),
            allow_users=list(self._allow_users),
            principal_ns=self.instance_id,
        )

    @staticmethod
    def _extract_text(blocks: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for block in blocks:
            block_type = block.get("type")
            if not block_type:
                continue
            payload = block.get(block_type)
            if not isinstance(payload, dict):
                continue
            for span in payload.get("rich_text", []):
                plain = span.get("plain_text")
                if plain:
                    parts.append(plain)
        return "\n".join(parts)
