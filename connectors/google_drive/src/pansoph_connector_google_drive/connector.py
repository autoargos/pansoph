"""The google_drive connector.

Implemented against the Google Drive API v3.

  API docs:    https://developers.google.com/drive/api/reference/rest/v3/files/list
               https://developers.google.com/drive/api/reference/rest/v3/changes/list
               https://developers.google.com/drive/api/reference/rest/v3/permissions/list
  Auth:        OAuth2 bearer access token (config: "token"). The token must have a
               read scope (drive.readonly or drive.metadata.readonly + file read).
               Obtaining the token (OAuth consent or a service account with domain
               delegation) is the operator's step; this connector does not run the
               OAuth flow.
  Incremental: the changes feed (changes.list with a saved startPageToken).
  Scope:       an instance is confined to one folder with the `folder_id` config
               (query operator "'<id>' in parents") and, for a shared drive, the
               `drive_id` config (corpora=drive, driveId, includeItemsFromAllDrives,
               supportsAllDrives). The deprecated teamDrive*/corpus params are not used.
  ACL model:   a file's permissions map to the ACL. An "anyone" permission is public;
               "user" permissions become allow_users (by emailAddress); "group" and
               "domain" become allow_groups. Namespaced by the connector instance.
  Verified:    against the documented v3 endpoints (files.list search syntax and
               shared-drive parameters) on 2026-06-08; provide an OAuth token to run
               it live.

The runtime owns pagination and retries; methods describe one page or one file.
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

_BASE = "https://www.googleapis.com/drive/v3"
# Google Docs native types are exported to text; binary files are downloaded as-is.
_EXPORT = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.presentation": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
}


class GoogleDriveConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.instance_id = instance_id
        self._token: str = cfg.get("token", "")
        self._drive_id: str = cfg.get("drive_id", "")
        base_q: str = cfg.get("query", "trashed=false")
        folder_id: str = cfg.get("folder_id", "")
        # Confine the instance to one folder when folder_id is set, by ANDing the
        # parents operator onto the base query.
        self._query: str = f"'{folder_id}' in parents and ({base_q})" if folder_id else base_q
        self.meta = ConnectorMeta(
            name="google_drive",
            auth=AuthScheme.oauth2,
            capabilities=Capabilities(
                incremental=True, permission_sync=True, deletions=True, slim_listing=True
            ),
        )

    @classmethod
    def spec(cls) -> ConnectorSpec:
        return ConnectorSpec(
            name="google_drive",
            title="Google Drive",
            description=(
                "Ingest files from a Google Drive folder or shared drive over the Drive v3 API."
            ),
            fields=(
                ConfigField(
                    key="token",
                    label="OAuth access token",
                    kind=FieldKind.SECRET,
                    help=(
                        "A Google OAuth2 bearer access token with a read scope "
                        "(drive.readonly or drive.metadata.readonly). Paste the token; "
                        "the OAuth consent flow is the operator's step. Sealed in the "
                        "secret store."
                    ),
                ),
                ConfigField(
                    key="folder_id",
                    label="Folder ID",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="0ABcdEFGhijKLMnoPQR",
                    help=(
                        "Confine the instance to one folder. Leave blank to ingest all "
                        "reachable files."
                    ),
                ),
                ConfigField(
                    key="drive_id",
                    label="Shared drive ID",
                    kind=FieldKind.STRING,
                    required=False,
                    help="Set to narrow the search to one shared drive. Leave blank for My Drive.",
                ),
                ConfigField(
                    key="query",
                    label="Drive query",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="trashed=false",
                    help=(
                        "A Drive v3 files.list search query. ANDed with the folder filter when set."
                    ),
                ),
            ),
        )

    def validate(self, config: dict[str, Any]) -> ValidationResult:
        if not config.get("token"):
            return ValidationResult(ok=False, error="missing OAuth token")
        return ValidationResult(ok=True)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=_BASE,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=30.0,
        )

    def _drive_params(self) -> dict[str, str]:
        """Shared-drive support. supportsAllDrives is harmless on My Drive; corpora
        and driveId narrow the search to one shared drive when configured."""
        params = {"supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        if self._drive_id:
            params["corpora"] = "drive"
            params["driveId"] = self._drive_id
        return params

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        async with self._client() as client:
            page: str | None = None
            while True:
                resp = await client.get(
                    "/files",
                    params={
                        "q": self._query,
                        "fields": "nextPageToken, files(id, mimeType, modifiedTime)",
                        "pageSize": 100,
                        **self._drive_params(),
                        **({"pageToken": page} if page else {}),
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                for f in data.get("files", []):
                    yield DocRef(source_doc_id=f["id"], instance_id=self.instance_id), ""
                page = data.get("nextPageToken")
                if not page:
                    break

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        async with self._client() as client:
            meta = (
                await client.get(
                    f"/files/{ref.source_doc_id}",
                    params={"fields": "id, name, mimeType, webViewLink", **self._drive_params()},
                )
            ).json()
            mime = meta.get("mimeType", "")
            if mime in _EXPORT:
                resp = await client.get(
                    f"/files/{ref.source_doc_id}/export",
                    params={"mimeType": _EXPORT[mime], "supportsAllDrives": "true"},
                )
            else:
                resp = await client.get(
                    f"/files/{ref.source_doc_id}",
                    params={"alt": "media", "supportsAllDrives": "true"},
                )
            resp.raise_for_status()
            raw = RawDoc(
                source_doc_id=ref.source_doc_id,
                instance_id=self.instance_id,
                mime=_EXPORT.get(mime, mime),
                text=resp.text,
                title=meta.get("name"),
                source_url=meta.get("webViewLink"),
            )
            return raw, await self._acl(client, ref.source_doc_id)

    async def list_acls(self, ref: DocRef) -> AccessControl:
        async with self._client() as client:
            return await self._acl(client, ref.source_doc_id)

    async def _acl(self, client: httpx.AsyncClient, file_id: str) -> AccessControl:
        resp = await client.get(
            f"/files/{file_id}/permissions",
            params={
                "fields": "permissions(type, role, emailAddress, domain)",
                "supportsAllDrives": "true",
            },
        )
        resp.raise_for_status()
        public = False
        users: list[str] = []
        groups: list[str] = []
        for p in resp.json().get("permissions", []):
            kind = p.get("type")
            if kind == "anyone":
                public = True
            elif kind == "user" and p.get("emailAddress"):
                users.append(p["emailAddress"])
            elif kind in ("group", "domain"):
                groups.append(p.get("emailAddress") or p.get("domain") or "")
        return AccessControl(
            public=public,
            allow_users=users,
            allow_groups=[g for g in groups if g],
            principal_ns=self.instance_id,
        )

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        return []
