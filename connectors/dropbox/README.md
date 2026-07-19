# pansoph-connector-dropbox

A pansoph connector that ingests files from Dropbox through the Dropbox HTTP API v2. It lists a folder recursively, downloads searchable file content, and maps each file's Dropbox sharing membership to a pansoph ACL so a document is only ever retrievable by the users and groups Dropbox says can actually read it. Verified against the official docs on 2026-07-05.

## What it syncs

Files under a configured folder path (recursive), listed via `files/list_folder`. Each entry's `path_lower` is the pansoph `source_doc_id`, the one identifier present on both live files and deletions.

On `fetch`, content is pulled only when it is worth indexing and small enough:

- Text and code files (`.txt`, `.md`, `.csv`, `.json`, `.yaml`, `.html`, `.py`, `.js`, `.go`, `.rs`, and similar) are decoded as UTF-8 and indexed as `text/plain`.
- Document files (`.pdf`, `.docx`, `.pptx`, `.xlsx`, `.epub`, and similar) are downloaded as bytes and handed to the parser.
- Anything else (images, audio, video, archives, binaries) is listed for its ACL but not downloaded.
- Files larger than 15 MB are never downloaded (their metadata carries the size).

## Setup

1. Create a Dropbox app at https://www.dropbox.com/developers/apps and note its **app key** and **app secret**.
2. Obtain a credential. Two paths are supported (see `validate`):
   - **Refresh token (preferred).** A Dropbox access token expires in about four hours. Provide a refresh token together with the app key and app secret; the connector mints a fresh access token at the start of each sync against `https://api.dropbox.com/oauth2/token` (`grant_type=refresh_token`), so the credential never goes stale.
   - **Static access token.** Provide a single OAuth 2 access token. The operator must rotate it by hand when it expires.

Instance config fields (from `DropboxConnector.spec()`):

| Key | Kind | Required | Purpose |
|---|---|---|---|
| `token` | secret | no | OAuth 2 access token (personal, team, or RBAC scoped). Leave empty when using refresh auth. |
| `refresh_token` | secret | no | OAuth 2 refresh token. Preferred over a static token. |
| `app_key` | string | no | Dropbox app key, used with the refresh token to mint access tokens. |
| `app_secret` | secret | no | Dropbox app secret. |
| `path` | string | no | Folder to list recursively. Empty lists from the root. |
| `base_url` | string | no | API base URL. Override only for a proxy or gateway. Defaults to `https://api.dropboxapi.com`. |

Fields declared with kind `secret` (`token`, `refresh_token`, `app_secret`) are sealed in the pansoph secret store by the platform and replaced in the stored config by a handle; they are never persisted as plaintext. `validate` requires either `token`, or the full trio `refresh_token` + `app_key` + `app_secret`.

## Required scopes / permissions

The connector does not assume or request a fixed scope set. It accepts whatever OAuth 2 credential the operator provides (a personal account token, a team scoped token, or any RBAC scoped credential) and maps only what that credential can reach. The credential must be able to call `files/list_folder`, `files/get_metadata`, `files/download`, and `sharing/list_file_members`.

## Permission model (ACL mapping)

For each file, the connector calls `sharing/list_file_members` (paged via `sharing/list_file_members/continue`) and maps membership to a pansoph `AccessControl`:

- A user or group is added to `allow` **only** when its `access_type` is one of `owner`, `editor`, `viewer`, or `viewer_no_comment`, the access levels that actually grant read.
- `traverse` and `no_access` allow navigation but not read, so they are excluded.
- Invitees are pending (no access is granted until the invite is accepted), so they are excluded.
- Users map by email; groups map by `group_name`.
- Dropbox exposes no document level deny, so `deny` stays empty.

This is fail-closed by construction: only members proven to hold a read grant become an allow, so the ACL never widens beyond who can actually read the file under the credential's reach. Each instance carries its own `principal_ns` (the `instance_id`), so a personal-token instance and a team-token instance never collide.

## Sync

Incremental sync is cursor based, using Dropbox folder cursors:

- **First sync** (`cursor is None`): a full recursive `files/list_folder` walk (paged via `files/list_folder/continue`) yields a `DocRef` per live file, then closes by yielding the go-forward cursor from `files/list_folder/get_latest_cursor` as the durable resume point.
- **Later syncs**: `files/list_folder/continue` replays the stored cursor and yields only entries changed since. The latest cursor Dropbox returns is paired with every yielded ref.
- **Deletions**: an entry tagged `deleted` is yielded as `DocRef(deleted=True)`, keyed by `path_lower` (a `DeletedMetadata` carries no `id`), so the runtime purges the document and its access tuples.

Webhooks only signal that an account changed and carry no per-file payload, so `on_webhook` returns no events; re-sync happens via the cursor path.

## Known limits

- Files larger than 15 MB are listed for their ACL but their content is not downloaded.
- Non-text, non-document file types are listed for their ACL only; their bytes are not fetched.
- A `429` (rate limit, with `Retry-After`) and a `409` (expired cursor reset) are surfaced, not hidden; the runtime owns backoff and cursor-reset recovery.

## Conformance

This connector passes the shared conformance suite (`assert_conforms` from `pansoph-connector-sdk`), run hermetically with `uv run pytest -m conformance`.

## References

- Dropbox HTTP API v2 reference: https://www.dropbox.com/developers/documentation/http/documentation
- Detecting Changes guide: https://developers.dropbox.com/detecting-changes-guide
- DBX Sharing guide: https://developers.dropbox.com/dbx-sharing-guide
- Python SDK reference (response shapes): https://dropbox-sdk-python.readthedocs.io/en/latest/api/files.html and https://dropbox-sdk-python.readthedocs.io/en/latest/api/sharing.html

API version: Dropbox HTTP API v2. Verified against the official docs on 2026-07-05.
