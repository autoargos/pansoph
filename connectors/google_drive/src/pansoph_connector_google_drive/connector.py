"""The google_drive connector.

Implemented against the Google Drive API v3.

  API docs:    https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list
               https://developers.google.com/workspace/drive/api/reference/rest/v3/changes/list
               https://developers.google.com/workspace/drive/api/reference/rest/v3/changes/getStartPageToken
               https://developers.google.com/workspace/drive/api/guides/manage-changes
               https://developers.google.com/workspace/drive/api/reference/rest/v3/permissions
               https://developers.google.com/workspace/drive/api/reference/rest/v3/permissions/list
               https://developers.google.com/workspace/drive/api/reference/rest/v3/drives/list
               https://developers.google.com/workspace/drive/api/guides/search-files
  Auth:        OAuth2 bearer access token (config: "token"). The connector declares
               an OAUTH config field (oauth_provider "google", scope drive.readonly),
               so the in-app Google OAuth flow (access_type=offline, prompt=consent)
               mints and seals the token; a token may also be pasted by hand into the
               same field. Verified against the Google Identity OAuth 2.0 web server
               docs and the Drive scopes doc on 2026-07-05:
               https://developers.google.com/identity/protocols/oauth2/web-server
               https://developers.google.com/workspace/drive/api/guides/api-specific-auth
               A service account with domain delegation remains a valid alternative.
  Incremental: the changes feed. A None cursor takes a fresh startPageToken from
               changes.getStartPageToken (with driveId + supportsAllDrives for a
               shared drive) and pages changes.list from it; that token is a durable,
               non-expiring high-water mark, so the very first sync already yields a
               real resume point rather than an empty string. Each page is fetched
               with includeRemoved=true, includeItemsFromAllDrives=true, and
               supportsAllDrives=true. Intra-listing paging threads nextPageToken;
               the durable checkpoint surfaced to the runtime is newStartPageToken,
               which the response carries only once the end of the current change
               list is reached. Verified 2026-07-05 that changes.list Change fields
               are changeType (file|drive), removed (a boolean: "Whether the file or
               shared drive has been removed from this list of changes, for example
               by deletion or loss of access"), fileId, driveId, time, and file (with
               file.trashed); getStartPageToken returns a non-expiring startPageToken.
  Deletions:  a change with removed==true (deletion or loss of access) or
               file.trashed==true is yielded as a DocRef with deleted=True (a
               tombstone). includeRemoved=true is required for removed changes to
               appear. Note that the changes feed is account/drive wide and has no
               folder filter, so folder_id narrows the initial full listing and
               fetch/ACL reads but does not filter the incremental change stream;
               tombstones for files outside the folder are harmless (the runtime only
               purges documents it already indexed).
  Scope:       an instance is confined to one folder with the `folder_id` config
               (query operator "'<id>' in parents") and, for a shared drive, the
               `drive_id` config (corpora=drive, driveId, includeItemsFromAllDrives,
               supportsAllDrives). The deprecated teamDrive*/corpus params are not used.
  ACL model:   a file's permissions map to the ACL, read via permissions.list and
               paginated on nextPageToken so a heavily shared file is never truncated
               (permissions.list returns at most 100 permissions per page for
               shared-drive files). type=anyone is public; type=user becomes
               allow_users by emailAddress; type=group becomes allow_groups by
               emailAddress; type=domain becomes allow_groups namespaced as
               "domain:<domain>" so a domain-wide grant is not conflated with a group
               of the same email. deny is always empty because Drive expresses no
               file-level deny grants. Namespaced by the connector instance. Verified
               2026-07-05 that Permission.type is one of user|group|domain|anyone and
               role is one of owner|organizer|fileOrganizer|writer|commenter|reader.
  Discovery:   list_resources powers the connect-then-pick UI. The top level (kind
               None) lists the shared drives the credential can reach via drives.list
               (returns Resource kind "drive", id = the drive id, which is also the id
               of the drive's top-level folder). Drilling into a drive or folder (kind
               "folder", parent = a drive or folder id) lists the child folders via
               files.list with a folder mimeType query and the "<parent> in parents"
               operator, plus supportsAllDrives / includeItemsFromAllDrives so shared
               drives are reachable. Both are read-only reads confined to what the
               drive.readonly token already permits, never a scope escalation. The
               opaque cursor is the source's own pageToken, passed back verbatim.
               drives.list: params pageSize (default 10, max 100), pageToken, q;
               response drives[] (id, name) plus nextPageToken. files.list: pageSize
               max 100; folder query "mimeType='application/vnd.google-apps.folder'
               and '<id>' in parents"; response files[] (id, name, mimeType) plus
               nextPageToken.
  Verified:    every endpoint and field above re-verified against the current Google
               Drive API v3 official docs on 2026-07-05; provide an OAuth token to run
               it live.

The runtime owns retries; methods describe one page or one file. list_changes owns
its own change-feed pagination and surfaces the durable checkpoint per the contract.
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
from pansoph_types import (
    AccessControl,
    AuthScheme,
    Capabilities,
    DocRef,
    RawDoc,
    Resource,
    ResourcePage,
)

_BASE = "https://www.googleapis.com/drive/v3"
# Google Docs native types are exported to text; binary files are downloaded as-is.
_EXPORT = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.presentation": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
}
_FOLDER_MIME = "application/vnd.google-apps.folder"


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
                incremental=True,
                permission_sync=True,
                deletions=True,
                slim_listing=True,
                resource_discovery=True,
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
                    label="Google Drive (OAuth)",
                    kind=FieldKind.OAUTH,
                    oauth_provider="google",
                    oauth_scopes=("https://www.googleapis.com/auth/drive.readonly",),
                    help=(
                        "Connect Google Drive with Google OAuth 2.0 (read only, "
                        "drive.readonly). The in-app flow seals the access token (and a "
                        "refresh token) into this field. You can still paste a current "
                        "bearer access token by hand; it is sealed in the secret store."
                    ),
                ),
                ConfigField(
                    key="folder_id",
                    label="Folder ID",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="0ABcdEFGhijKLMnoPQR",
                    resource_kind="folder",
                    help=(
                        "Confine the instance to one folder. Pick one from the folder tree "
                        "the connected credential can reach, or leave blank to ingest all "
                        "reachable files. Free-text folder ids still work."
                    ),
                ),
                ConfigField(
                    key="drive_id",
                    label="Shared drive ID",
                    kind=FieldKind.STRING,
                    required=False,
                    resource_kind="drive",
                    help=(
                        "Pick a shared drive the credential can reach to narrow the search "
                        "to it. Leave blank for My Drive. Free-text drive ids still work."
                    ),
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

    def _changes_params(self) -> dict[str, str]:
        """Shared-drive support for the changes feed. driveId scopes the feed to one
        shared drive; supportsAllDrives + includeItemsFromAllDrives keep My Drive and
        shared-drive changes visible; includeRemoved is required so deletions and
        loss-of-access changes appear as removed==true tombstones."""
        params: dict[str, str] = {
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "includeRemoved": "true",
        }
        if self._drive_id:
            params["driveId"] = self._drive_id
        return params

    async def _start_page_token(self, client: httpx.AsyncClient) -> str:
        """Fetch a fresh, non-expiring startPageToken from changes.getStartPageToken.

        Used when the runtime has no saved cursor (the initial sync), so even a first
        pass records a durable high-water mark instead of an empty string.
        """
        params: dict[str, str] = {"supportsAllDrives": "true"}
        if self._drive_id:
            params["driveId"] = self._drive_id
        resp = await client.get("/changes/startPageToken", params=params)
        resp.raise_for_status()
        token: str = resp.json().get("startPageToken", "")
        return token

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        """Page the Drive changes feed, yielding one DocRef per change.

        A None cursor takes a fresh startPageToken (the initial sync); otherwise the
        saved cursor is the durable resume point. Within a poll, nextPageToken threads
        intra-listing pages; the cursor yielded with every DocRef is the durable
        checkpoint (newStartPageToken once the end of the change list is reached, else
        the page token still being consumed) so the runtime always persists a
        non-empty, resumable high-water mark. removed==true or file.trashed==true maps
        to a deleted DocRef (a tombstone).
        """
        async with self._client() as client:
            page: str = cursor if cursor else await self._start_page_token(client)
            # The durable checkpoint carried alongside each yielded ref. It advances
            # to newStartPageToken when the source signals the change list is drained,
            # so a crash resumes from the last persisted checkpoint, never from zero.
            checkpoint: str = page
            while True:
                resp = await client.get(
                    "/changes",
                    params={
                        "pageToken": page,
                        "pageSize": 100,
                        "fields": (
                            "nextPageToken, newStartPageToken, "
                            "changes(changeType, removed, fileId, file(trashed))"
                        ),
                        **self._changes_params(),
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                next_page = data.get("nextPageToken")
                new_start = data.get("newStartPageToken")
                # Advance the durable checkpoint. Prefer newStartPageToken (the true
                # high-water mark); until it appears, the next intra-listing pageToken
                # is still a valid, non-empty resume point for this poll.
                checkpoint = new_start or next_page or checkpoint
                for change in data.get("changes", []):
                    file_id = change.get("fileId")
                    if not file_id:
                        continue
                    file_obj = change.get("file") or {}
                    deleted = bool(change.get("removed")) or bool(file_obj.get("trashed"))
                    yield (
                        DocRef(
                            source_doc_id=file_id,
                            instance_id=self.instance_id,
                            deleted=deleted,
                        ),
                        checkpoint,
                    )
                if not next_page:
                    break
                page = next_page

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
        """Build the ACL from the file's full permission list.

        permissions.list is paginated on nextPageToken (up to 100 permissions per page
        for shared-drive files), so every page is accumulated before the ACL is built;
        truncating to the first page would under-share (over-deny) a heavily shared
        file, which rule 1 forbids. type=domain is namespaced "domain:<domain>" so a
        domain-wide grant is not conflated with a group of the same email.
        """
        public = False
        users: list[str] = []
        groups: list[str] = []
        page_token: str | None = None
        while True:
            params: dict[str, str] = {
                "fields": "nextPageToken, permissions(type, role, emailAddress, domain)",
                "pageSize": "100",
                "supportsAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token
            resp = await client.get(f"/files/{file_id}/permissions", params=params)
            resp.raise_for_status()
            data = resp.json()
            for p in data.get("permissions", []):
                kind = p.get("type")
                if kind == "anyone":
                    public = True
                elif kind == "user" and p.get("emailAddress"):
                    users.append(p["emailAddress"])
                elif kind == "group" and p.get("emailAddress"):
                    groups.append(p["emailAddress"])
                elif kind == "domain" and p.get("domain"):
                    groups.append(f"domain:{p['domain']}")
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return AccessControl(
            public=public,
            allow_users=users,
            allow_groups=groups,
            principal_ns=self.instance_id,
        )

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        return []

    async def list_resources(
        self,
        kind: str | None = None,
        parent_id: str | None = None,
        cursor: str | None = None,
    ) -> ResourcePage:
        """Enumerate the drives and folders the drive.readonly credential can reach.

        Top level (kind None): the shared drives via drives.list, returned as Resource
        kind "drive". A drive id doubles as the id of its top-level folder, so drilling
        into it (kind "folder", parent = the drive/folder id) lists that node's child
        folders via files.list with a folder mimeType query and the parents operator.
        supportsAllDrives / includeItemsFromAllDrives keep shared-drive folders visible.
        The opaque cursor is Google's own pageToken, so pagination is the source's model
        and next_cursor is None exactly when the source stops returning nextPageToken.
        """
        async with self._client() as client:
            if kind is None or kind == "drive":
                return await self._list_drives(client, cursor)
            return await self._list_folders(client, parent_id, cursor)

    async def _list_drives(self, client: httpx.AsyncClient, cursor: str | None) -> ResourcePage:
        params: dict[str, str] = {
            "pageSize": "100",
            "fields": "nextPageToken, drives(id, name)",
        }
        if cursor:
            params["pageToken"] = cursor
        resp = await client.get("/drives", params=params)
        resp.raise_for_status()
        data = resp.json()
        items = [
            Resource(id=d["id"], kind="drive", label=d.get("name") or d["id"])
            for d in data.get("drives", [])
            if d.get("id")
        ]
        return ResourcePage(items=items, next_cursor=data.get("nextPageToken"))

    async def _list_folders(
        self, client: httpx.AsyncClient, parent_id: str | None, cursor: str | None
    ) -> ResourcePage:
        # Without a parent there is no folder tree to walk (the top level is drives).
        if not parent_id:
            return ResourcePage(items=[], next_cursor=None)
        q = f"mimeType = '{_FOLDER_MIME}' and '{parent_id}' in parents and trashed = false"
        params: dict[str, str] = {
            "q": q,
            "pageSize": "100",
            "fields": "nextPageToken, files(id, name)",
            **self._drive_params(),
        }
        if cursor:
            params["pageToken"] = cursor
        resp = await client.get("/files", params=params)
        resp.raise_for_status()
        data = resp.json()
        items = [
            Resource(
                id=f["id"],
                kind="folder",
                label=f.get("name") or f["id"],
                parent_id=parent_id,
            )
            for f in data.get("files", [])
            if f.get("id")
        ]
        return ResourcePage(items=items, next_cursor=data.get("nextPageToken"))
