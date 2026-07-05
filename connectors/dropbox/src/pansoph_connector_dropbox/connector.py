"""The dropbox connector.

Implemented against the Dropbox HTTP API v2. Verified against the official docs on
2026-07-05:

  Detecting Changes guide:
      https://developers.dropbox.com/detecting-changes-guide
      Folder cursors are pointers to a folder at a point in time.
      files/list_folder/get_latest_cursor returns an opaque cursor for "changes
      going forward" without enumerating existing files. files/list_folder/continue,
      called with a stored cursor, reports items changed after the cursor, and a
      removal surfaces as an entry with ".tag" == "deleted" (a DeletedMetadata).
      That DeletedMetadata stream is the only deletion signal Dropbox exposes for
      polling. Cursors are long lived; always persist the latest returned cursor.
      An expired cursor returns a 409 reset error (the runtime owns that recovery).
  HTTP reference:
      https://www.dropbox.com/developers/documentation/http/documentation
      files-list_folder, files-list_folder-continue,
      files-list_folder-get_latest_cursor, sharing-list_file_members,
      sharing-list_file_members-continue.
  DBX Sharing guide:
      https://developers.dropbox.com/dbx-sharing-guide
  Python SDK reference (authoritative response shapes):
      https://dropbox-sdk-python.readthedocs.io/en/latest/api/files.html
          DeletedMetadata has name, path_lower, path_display,
          parent_shared_folder_id, preview_url. It has NO id field, so path_lower
          is the only stable identifier a deleted entry carries. FileMetadata and
          FolderMetadata carry both id and path_lower.
      https://dropbox-sdk-python.readthedocs.io/en/latest/api/sharing.html
          sharing/list_file_members returns SharedFileMembers with users, groups,
          invitees, cursor. users are UserFileMembershipInfo (access_type, user,
          is_inherited); user is UserInfo (account_id, email, display_name,
          same_team). groups are GroupMembershipInfo (access_type, group); group is
          GroupInfo (group_name, group_id, member_count). invitees are
          InviteeMembershipInfo (access_type, invitee=InviteeInfo(email)) and are
          PENDING (no access is granted until the invite is accepted).
          access_type is an AccessLevel: owner, editor, viewer, viewer_no_comment,
          traverse, no_access.

  Auth:        OAuth 2 bearer token, operator provided (config "token"). The token
               may be a personal account token, a team scoped token, or any RBAC
               scoped credential. The connector does not assume a scope; it maps
               whatever the credential can reach.
  Transport:   every call is an HTTPS POST to https://api.dropboxapi.com/2 with a
               JSON body. Headers: Authorization: Bearer <token>,
               Content-Type: application/json.
  Identifier:  source_doc_id is the entry's path_lower. This is the one identifier
               present on both live entries (FileMetadata) and removals
               (DeletedMetadata), so a deletion can be reconciled against the live
               document it removes. get_metadata and list_file_members are called by
               path, which path_lower satisfies.
  Pagination:  cursor based. files/list_folder returns "entries", "cursor", and
               "has_more"; when has_more is true the caller continues with
               files/list_folder/continue using the cursor. sharing/list_file_members
               pages the same way via sharing/list_file_members/continue.
  Incremental: the list_folder cursor is the durable resume point. On the first sync
               (cursor is None) the connector performs a full list_folder walk and
               ends by yielding the latest cursor via get_latest_cursor. On a later
               sync it calls files/list_folder/continue with the stored cursor and
               yields only entries changed since, including deletions.
  Deletions:   an entry with ".tag" == "deleted" is yielded as a
               DocRef(deleted=True) keyed by its path_lower, so the runtime purges
               the document and its access tuples.
  Rate limit:  Dropbox returns 429 with Retry-After; surfaced, not hidden. The
               runtime owns backoff.
  ACL model:   sharing/list_file_members returns the users, groups, and invitees a
               file is shared with. A user or group is mapped to an allow only when
               its access_type actually grants read (owner, editor, viewer, or
               viewer_no_comment). traverse and no_access grant navigation but not
               read, so they are excluded. Invitees are pending (not yet members) and
               grant no access, so they are excluded. Dropbox has no document level
               deny, so deny stays empty. The ACL therefore reflects who can actually
               read the file under the credential's reach, never a wider grant.

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
from pansoph_connector_sdk import (
    ChangeEvent,
    ConfigField,
    ConnectorMeta,
    ConnectorSpec,
    FieldKind,
    ValidationResult,
)
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc

# AccessLevel values (sharing/list_file_members access_type) that grant read.
# owner, editor, viewer, and viewer_no_comment can all read the file. traverse and
# no_access can navigate to the item but cannot read it, so they are not a grant.
_READ_ACCESS_TYPES: frozenset[str] = frozenset({"owner", "editor", "viewer", "viewer_no_comment"})


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
                incremental=True, permission_sync=True, deletions=True, slim_listing=True
            ),
        )

    @classmethod
    def spec(cls) -> ConnectorSpec:
        return ConnectorSpec(
            name="dropbox",
            title="Dropbox",
            description="Ingest files from Dropbox via the HTTP API v2 with an OAuth 2 token.",
            fields=(
                ConfigField(
                    key="token",
                    label="Access token",
                    kind=FieldKind.SECRET,
                    help=(
                        "Dropbox OAuth 2 bearer token (personal, team, or RBAC scoped). "
                        "Sealed in the secret store."
                    ),
                ),
                ConfigField(
                    key="path",
                    label="Folder path",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="/Team Docs",
                    help="Folder to list recursively. Leave empty to list from the root.",
                ),
                ConfigField(
                    key="base_url",
                    label="API base URL",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="https://api.dropboxapi.com",
                    help="Override only for a proxy or self-managed gateway.",
                ),
            ),
        )

    # --- contract ---
    def validate(self, config: dict[str, Any]) -> ValidationResult:
        if not config.get("token"):
            return ValidationResult(ok=False, error="missing config: token")
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        """Yield (DocRef, next_cursor) for everything changed since `cursor`.

        On the first sync (cursor is None) walk the whole folder via files/list_folder
        and files/list_folder/continue, yielding a DocRef per live file, then close by
        yielding the durable go-forward cursor from files/list_folder/get_latest_cursor
        so the next sync resumes incrementally.

        On a later sync (cursor is a stored string) call files/list_folder/continue with
        it and yield only entries changed since, including removals: an entry with
        ".tag" == "deleted" is yielded as DocRef(deleted=True), keyed by path_lower, so
        the runtime purges it. Every yielded cursor is the latest one Dropbox returned,
        which is the durable resume point.
        """
        async with self._client() as client:
            if cursor is None:
                async for item in self._initial_listing(client):
                    yield item
            else:
                async for item in self._continue_listing(client, cursor):
                    yield item

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

    def _ref(self, source_doc_id: str, deleted: bool = False) -> DocRef:
        return DocRef(source_doc_id=source_doc_id, instance_id=self.instance_id, deleted=deleted)

    async def _initial_listing(
        self, client: httpx.AsyncClient
    ) -> AsyncIterator[tuple[DocRef, str]]:
        """Full first pass: walk every page, then close on the go-forward cursor.

        A full list_folder walk enumerates existing files but its per-page cursors are
        not the durable resume point (they still replay the remaining listing). Only
        after the walk is exhausted do we fetch the go-forward cursor from
        get_latest_cursor and pair it with each yielded ref, so the next sync starts
        from a clean incremental point.
        """
        resp = await client.post(
            "/2/files/list_folder",
            json={"path": self._path, "recursive": True},
        )
        resp.raise_for_status()
        payload = resp.json()

        live_ids: list[str] = []
        while True:
            for entry in payload.get("entries", []):
                if entry.get(".tag") == "file":
                    path = entry.get("path_lower")
                    if path:
                        live_ids.append(path)
            if not payload.get("has_more"):
                break
            resp = await client.post(
                "/2/files/list_folder/continue",
                json={"cursor": payload.get("cursor", "")},
            )
            resp.raise_for_status()
            payload = resp.json()

        forward_cursor = await self._latest_cursor(client)
        for path in live_ids:
            yield self._ref(path), forward_cursor

    async def _continue_listing(
        self, client: httpx.AsyncClient, cursor: str
    ) -> AsyncIterator[tuple[DocRef, str]]:
        """Incremental pass: replay the stored cursor and emit only changes.

        A ".tag" == "deleted" entry becomes a DocRef(deleted=True) keyed by path_lower
        (DeletedMetadata carries no id, so path_lower is the only stable key). A
        ".tag" == "file" entry is a create or modify and is yielded live. Folder
        entries and other tags are ignored. The cursor advances page by page and the
        latest one is what we pair with every yielded ref.
        """
        current = cursor
        while True:
            resp = await client.post(
                "/2/files/list_folder/continue",
                json={"cursor": current},
            )
            resp.raise_for_status()
            payload = resp.json()
            current = payload.get("cursor", current)
            for entry in payload.get("entries", []):
                tag = entry.get(".tag")
                path = entry.get("path_lower")
                if not path:
                    continue
                if tag == "deleted":
                    yield self._ref(path, deleted=True), current
                elif tag == "file":
                    yield self._ref(path), current
            if not payload.get("has_more"):
                break

    async def _latest_cursor(self, client: httpx.AsyncClient) -> str:
        resp = await client.post(
            "/2/files/list_folder/get_latest_cursor",
            json={"path": self._path, "recursive": True},
        )
        resp.raise_for_status()
        return str(resp.json().get("cursor", ""))

    async def _acl(self, client: httpx.AsyncClient, file_path: str) -> AccessControl:
        """Map sharing/list_file_members to an AccessControl faithful to who can read.

        Only members whose access_type grants read (owner, editor, viewer,
        viewer_no_comment) are allowed. Invitees are pending and excluded. The member
        list is paged through sharing/list_file_members/continue so a widely shared file
        is not truncated to its first page.
        """
        users: list[str] = []
        groups: list[str] = []

        resp = await client.post(
            "/2/sharing/list_file_members",
            json={"file": file_path},
        )
        resp.raise_for_status()
        payload = resp.json()

        while True:
            for member in payload.get("users", []):
                if member.get("access_type", {}).get(".tag") not in _READ_ACCESS_TYPES:
                    continue
                email = member.get("user", {}).get("email")
                if email:
                    users.append(email)
            for member in payload.get("groups", []):
                if member.get("access_type", {}).get(".tag") not in _READ_ACCESS_TYPES:
                    continue
                name = member.get("group", {}).get("group_name")
                if name:
                    groups.append(name)
            # invitees are pending (no access until accepted), so they are not a grant.

            next_cursor = payload.get("cursor")
            if not next_cursor:
                break
            resp = await client.post(
                "/2/sharing/list_file_members/continue",
                json={"cursor": next_cursor},
            )
            resp.raise_for_status()
            payload = resp.json()

        return AccessControl(allow_users=users, allow_groups=groups, principal_ns=self.instance_id)
