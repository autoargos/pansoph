# pansoph-connector-google-drive

A pansoph connector that ingests files from Google Drive over the Drive v3 REST API. An instance covers My Drive or a shared drive, optionally confined to one or more folder subtrees, and it syncs file content plus each file's permission model into pansoph's access-controlled index.

## What it syncs

- Every non-folder file the credential can reach, scoped by the instance configuration. Folders are traversed, never ingested as documents.
- Google native documents are exported to text: Docs to `text/plain`, Slides to `text/plain`, Sheets to `text/csv`. All other files are downloaded as-is via `alt=media`.
- Per file, the connector records title (`name`), source URL (`webViewLink`), and MIME type.
- Scope selection: leave the folder and drive fields blank to ingest all reachable files under My Drive. Set `drive_id` to narrow to one shared drive. Set one or more `folder_id` values to confine the instance to those folder subtrees (each pick adds its whole descendant tree).

## Setup

An operator connects an instance through the in-app Google OAuth 2.0 flow (authorization-code grant with `access_type=offline` and `prompt=consent`, so a refresh token is minted on every connect).

1. Create an OAuth 2.0 Web Application client in the Google Cloud Console (APIs and Services, Credentials). Enable the Google Drive API for the project.
2. Register the pansoph redirect URI on that client. pansoph uses the pattern `https://<your-pansoph-host>/api/oauth/google/callback` (the provider segment is `google`).
3. Provide the client credentials to agora via the env keys `PANSOPH_OAUTH_GOOGLE_CLIENT_ID` and `PANSOPH_OAUTH_GOOGLE_CLIENT_SECRET` (or the secret store under `_oauth/google/client_id` and `_oauth/google/client_secret`). The OAuth app is shared across Google connectors; each connector requests only its own data scope.
4. In pansoph, add a Google Drive instance and click connect. The flow seals the access token (and refresh token) into the connector's `token` field in the secret store. A current bearer access token may also be pasted by hand into the same field.
5. Pick folders with the multi-folder picker. The picker enumerates the drives and folders the connected credential can reach (`list_resources`): the top level lists shared drives plus a synthetic non-selectable "My Drive" root you can drill into, and drilling into a drive or folder lists its child folders. Free-text folder and drive ids also work.

## Required scopes / permissions

- `https://www.googleapis.com/auth/drive.readonly`

The shared Google OAuth app also carries the minimal identity scopes `openid`, `email`, and `profile` for sign-in; the Drive connector itself declares only `drive.readonly` and never requests write access.

## Permission model (ACL mapping)

Each file's ACL is built from its full permission list, read via `files.permissions.list` and paginated on `nextPageToken` so a heavily shared file is never truncated (the API returns at most 100 permissions per page for shared-drive files). Every page is accumulated before the ACL is built, so a truncated read can never under-share (over-deny). The mapping, by permission `type`:

- `anyone` sets the document public.
- `user` becomes an allowed user, keyed by `emailAddress`.
- `group` becomes an allowed group, keyed by `emailAddress`.
- `domain` becomes an allowed group namespaced as `domain:<domain>`, so a domain-wide grant is never conflated with a group of the same email.

The ACL is namespaced by the connector instance (`principal_ns = instance_id`). Deny is always empty because Drive expresses no file-level deny grants. Permissions with no usable identifier are dropped rather than guessed.

## Sync

- Initial backfill: the first sync (a `None` cursor) first fetches a go-forward `startPageToken` from `changes.getStartPageToken`, then lists every existing file via `files.list`. The changes feed only reports changes after its start token, so backfilling from `files.list` is what makes the first sync index the corpus instead of reporting zero documents. With folders configured, each folder subtree is walked (Drive has no single query for all descendants, so subfolders are recursed) over a shared seen-set; otherwise the whole corpus matching the base query is listed. `files.list` pages on `nextPageToken` at `pageSize` 100.
- Incremental cursor: every backfilled ref carries that `startPageToken` as its durable, non-expiring resume point. On later syncs the connector polls `changes.list` from the saved cursor. The durable checkpoint surfaced to the runtime is `newStartPageToken`, which the response carries only once the change list is drained; until then the next `nextPageToken` is a valid intra-poll resume point.
- Folder confinement on incrementals: the changes feed is account or drive wide with no folder filter, so a live changed file is kept only when its parent chain resolves under a configured folder (the immediate parents already present in the change avoid an extra call in the common case).
- Deletions: a change with `removed == true` (deletion or loss of access) or `file.trashed == true` is yielded as a tombstone `DocRef` with `deleted = True`. `includeRemoved=true` is set so removed changes appear, and a deletion is always surfaced regardless of folder scope (the runtime only purges what it already indexed).
- Shared drives: the changes feed passes `driveId` when configured, plus `supportsAllDrives`, `includeItemsFromAllDrives`, and `includeRemoved`.

## Known limits

- The Drive changes feed is account or drive wide and has no folder filter, so incremental folder confinement is done client side by walking each changed file's parent chain.
- Drive has no single query for all descendants of a folder, so folder-scoped backfill recurses the subtree folder by folder.

## Conformance

This connector passes the shared conformance suite (`assert_conforms` from `pansoph-connector-sdk`), run hermetically with `uv run pytest -m conformance`.

## References

Implemented against the Google Drive API v3 and Google Identity OAuth 2.0. Endpoints and fields verified against the official docs on 2026-07-05.

- files.list: https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list
- changes.list: https://developers.google.com/workspace/drive/api/reference/rest/v3/changes/list
- changes.getStartPageToken: https://developers.google.com/workspace/drive/api/reference/rest/v3/changes/getStartPageToken
- Manage changes guide: https://developers.google.com/workspace/drive/api/guides/manage-changes
- permissions: https://developers.google.com/workspace/drive/api/reference/rest/v3/permissions
- permissions.list: https://developers.google.com/workspace/drive/api/reference/rest/v3/permissions/list
- drives.list: https://developers.google.com/workspace/drive/api/reference/rest/v3/drives/list
- Search files: https://developers.google.com/workspace/drive/api/guides/search-files
- Drive API-specific auth and scopes: https://developers.google.com/workspace/drive/api/guides/api-specific-auth
- OAuth 2.0 web server flow: https://developers.google.com/identity/protocols/oauth2/web-server
