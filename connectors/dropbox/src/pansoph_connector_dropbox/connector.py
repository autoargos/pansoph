"""The dropbox connector.

Implemented against the Dropbox HTTP API v2. Verified against the official docs:

  API docs:    https://www.dropbox.com/developers/documentation/http/documentation
               https://www.dropbox.com/developers/documentation/http/documentation#files-list_folder
               https://www.dropbox.com/developers/documentation/http/documentation#files-list_folder-continue
               https://www.dropbox.com/developers/documentation/http/documentation#files-get_metadata
               https://www.dropbox.com/developers/documentation/http/documentation#sharing-list_file_members
  Auth:        OAuth 2 bearer token, operator provided (config "token"). The token
               may be a personal account token, a team scoped token, or any RBAC
               scoped credential. The connector does not assume a scope; it maps
               whatever the credential can reach.
  Transport:   every call is an HTTPS POST to https://api.dropboxapi.com/2 with a
               JSON body. Headers: Authorization: Bearer <token>,
               Content-Type: application/json.
  Pagination:  cursor based. files/list_folder returns "entries", "cursor", and
               "has_more"; when has_more is true the caller continues with
               files/list_folder/continue using the cursor.
  Incremental: the list_folder cursor is the durable resume point. A later sync
               passes the stored cursor to files/list_folder/continue to receive
               only entries changed since. First cut performs a full listing each
               run; cursor-filtered incremental (Dropbox delta cursors via
               list_folder/continue) is a follow-up.
  Rate limit:  Dropbox returns 429 with Retry-After; surfaced, not hidden. The
               runtime owns backoff.
  ACL model:   sharing/list_file_members returns the users and groups a file is
               shared with. users[].user.email maps to allow_users, and
               groups[].group.group_name maps to allow_groups, namespaced by the
               instance. Dropbox has no document level deny, so deny stays empty.
               Because access is RBAC controlled, the ACL reflects the
               credential's actual reach, not an assumption.
  Verified on: 2026-06-20.

Credential scope note: every configured instance carries its own credential and
its own ACL namespace (principal_ns = instance_id). One instance may be a personal
token, another a team scoped credential. They never collide, and each maps ACLs at
the granularity its credential grants. See pansoph CLAUDE.md section 6.

The runtime owns retries, backoff, and resumable sync, so a method only describes
how to talk to Dropbox for one page or one document. Content download is downstream;
fetch here returns metadata so ingestion can stage and the ACL is bound at fetch.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
from pansoph_connector_sdk import ChangeEvent, ConnectorMeta, ValidationResult
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc


class DropboxConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        self.instance_id = instance_id
        cfg = config or {}
        self._token: str = cfg.get("token", "")
        self._path: str = cfg.get("path", "")
        self._base_url: str = cfg.get("base_url", "https://api.dropboxapi.com")
        self.meta = ConnectorMeta(
            name="dropbox",
            auth=AuthScheme.oauth2,
            capabilities=Capabilities(
                incremental=False, permission_sync=True, deletions=True, slim_listing=True
            ),
        )

    # --- contract ---
    def validate(self, config: dict[str, Any]) -> ValidationResult:
        if not config.get("token"):
            return ValidationResult(ok=False, error="missing config: token")
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        # Full listing each run: read the first page, then follow has_more through
        # files/list_folder/continue until the listing is exhausted. Incremental is
        # off, so the yielded cursor is unused and we surface an empty string.
        async with self._client() as client:
            resp = await client.post(
                "/2/files/list_folder",
                json={"path": self._path, "recursive": True},
            )
            resp.raise_for_status()
            payload = resp.json()
            while True:
                for entry in payload.get("entries", []):
                    if entry.get(".tag") == "file":
                        yield self._ref(entry["id"]), ""
                if not payload.get("has_more"):
                    break
                resp = await client.post(
                    "/2/files/list_folder/continue",
                    json={"cursor": payload.get("cursor", "")},
                )
                resp.raise_for_status()
                payload = resp.json()

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        async with self._client() as client:
            resp = await client.post(
                "/2/files/get_metadata",
                json={"path": ref.source_doc_id},
            )
            resp.raise_for_status()
            payload = resp.json()
            raw = RawDoc(
                source_doc_id=ref.source_doc_id,
                instance_id=self.instance_id,
                mime="application/octet-stream",
                text=None,
                title=payload.get("name"),
            )
            return raw, await self._acl(client, ref.source_doc_id)

    async def list_acls(self, ref: DocRef) -> AccessControl:
        async with self._client() as client:
            return await self._acl(client, ref.source_doc_id)

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        # Dropbox webhooks only signal that an account changed; the connector then
        # re-syncs via list_folder/continue. There is no per-file payload to map.
        return []

    # --- helpers ---
    def _client(self) -> httpx.AsyncClient:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=30.0)

    def _ref(self, source_doc_id: str) -> DocRef:
        return DocRef(source_doc_id=source_doc_id, instance_id=self.instance_id)

    async def _acl(self, client: httpx.AsyncClient, file_id: str) -> AccessControl:
        resp = await client.post(
            "/2/sharing/list_file_members",
            json={"file": file_id},
        )
        resp.raise_for_status()
        payload = resp.json()
        users = [
            email
            for member in payload.get("users", [])
            if (email := member.get("user", {}).get("email"))
        ]
        groups = [
            name
            for member in payload.get("groups", [])
            if (name := member.get("group", {}).get("group_name"))
        ]
        return AccessControl(allow_users=users, allow_groups=groups, principal_ns=self.instance_id)
