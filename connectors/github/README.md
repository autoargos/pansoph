# pansoph-connector-github

A pansoph connector for GitHub. It ingests textual files from a single GitHub repository over the GitHub REST API and maps the repository's permission model to pansoph document-level ACLs. Each configured instance carries its own credential and its own ACL namespace (the instance id), so a personal token, an org installation, and a team-scoped credential never collide.

## What it syncs

- Textual files from one repository, read from the default branch or a configured `branch`.
- Only files whose extension is in the connector's textual allowlist are ingested: `.md`, `.markdown`, `.rst`, `.txt`, `.adoc`, `.py`, `.js`, `.ts`, `.tsx`, `.jsx`, `.go`, `.rs`, `.java`, `.rb`, `.c`, `.h`, `.cpp`, `.cs`, `.json`, `.yaml`, `.yml`, `.toml`, `.ini`, `.cfg`, `.html`, `.css`, `.sql`, `.sh`, `.csv`. Binary and other files are skipped.
- Each document's text is the decoded file content, its title is the file name, and its source URL is the file's `html_url` on GitHub.
- Ingestion is bounded by `path` (a path prefix filter) and `max_files` (a per-sync cap, default 300) so large repositories respect the token's rate limit.

## Setup

An operator connects an instance with the config fields defined by the connector spec: `owner`, `repo`, `token` (optional), `branch` (optional), `path` (optional), `max_files` (optional), and `base_url` (optional).

- `owner` and `repo` are required. `repo` may be a bare name (`abacus`) with a separate `owner`, or a full name (`cyproto/abacus`), in which case the owner is split out of the full name.
- `base_url` defaults to `https://api.github.com`. Override it only for GitHub Enterprise or a self-managed gateway.
- The `token` field is optional. Public repositories are readable unauthenticated (at a lower rate limit). Private repositories require a token.

Two ways to supply the token:

1. OAuth. The `token` field uses the `github` OAuth provider. pansoph builds the redirect URL as `{public_base_url}/api/oauth/github/callback` (the routes are `GET /api/oauth/github/authorize` and `GET /api/oauth/github/callback`). Register an OAuth app (or GitHub App) with GitHub, then set the client credentials in the environment as `PANSOPH_OAUTH_GITHUB_CLIENT_ID` and `PANSOPH_OAUTH_GITHUB_CLIENT_SECRET`. The authorize endpoint is `https://github.com/login/oauth/authorize` and the token endpoint is `https://github.com/login/oauth/access_token`. S256 PKCE is used.

2. Manual token. Paste a personal access token, or an org or GitHub App installation token, into the `token` field. It is sealed in the secret store under the same key as the OAuth path.

## Required scopes / permissions

The read-oriented default OAuth scopes are:

- `repo` (the only scope that reads private repository contents; GitHub has no read-only private-repo scope)
- `read:org` (read-only, backs the team ACL sync)
- `read:user` (read-only, backs the collaborator ACL sync)

An instance may narrow these (for example `public_repo` for public-only) via `ConfigField.oauth_scopes`. The connector does not assume a scope; it maps whatever the credential can actually reach.

## Permission model (ACL mapping)

The ACL is computed once per sync at the repository level and reused for every file. It maps `GET /repos/{owner}/{repo}` visibility to pansoph ACLs, and it fails closed:

- Only an explicit `visibility == "public"` maps to a public (world-readable) ACL. A missing or ambiguous `visibility`/`private` is never treated as public, because `base_url` is configurable and an untrusted gateway could return a trimmed body. Inferring public from an absence could over-share a private repository, so it is forbidden.
- `visibility == "internal"` (a GitHub Enterprise internal repo, readable by every enterprise member) maps to a synthetic enterprise-members group `enterprise:{owner}:members`, anchored on the owning org and namespaced by the instance. It is not mapped to public.
- A private repo, or any body without the explicit public/internal signal (the fail-closed default), maps to the collaborators and teams the credential can see: `allow_users` are collaborator logins from `GET /repos/{owner}/{repo}/collaborators?affiliation=all`, and `allow_groups` are team slugs from `GET /repos/{owner}/{repo}/teams`.

Granularity is user (collaborator login), group (team slug), and the synthetic enterprise-members group. Every ACL is namespaced by `principal_ns = instance_id`. A partial credential under-shares (a UX issue), it never over-shares (a security bug), so every ambiguous case resolves to the narrower audience.

## Sync

- Initial backfill. With no cursor, the connector lists the current tree via `GET /repos/{owner}/{repo}/git/trees/{branch}?recursive=1`, filters to textual blobs (and the `path` prefix), and yields each as an upsert up to `max_files`. A full crawl reports no removals.
- Incremental. With a cursor (an ISO 8601 commit date), the connector lists commits via `GET /repos/{owner}/{repo}/commits?since={cursor}`, follows the `Link` `rel="next"` header, and reads each commit's changed files via `GET /repos/{owner}/{repo}/commits/{sha}`. The cursor advances to the latest commit date. Commits are listed newest first, so the first status seen for a path is its latest state.
- Deletions. A file whose most recent commit status in the window is `removed` is surfaced as a deleted `DocRef` (a tombstone) so the runtime purges that document and its access tuples. Push webhooks (`on_webhook`) map each commit's `added` and `modified` paths to upserts and `removed` paths to deletes.
- Pagination is GitHub's own: `per_page` up to 100, following the `Link` `rel="next"` header. The opaque resource-discovery cursor is GitHub's page number.

## Known limits

- Only files with an extension in the textual allowlist are ingested; all other files are skipped.
- Ingestion is capped at `max_files` per sync (default 300) and, when set, restricted to the `path` prefix, to respect the token's rate limit.
- GitHub's authenticated rate limit is 5000 requests per hour; it is surfaced, not hidden.
- The unit of access control is the repository: the ACL is repo-level and applies to every ingested file.

## Conformance

This connector passes the shared conformance suite (`assert_conforms` from `pansoph-connector-sdk`), run hermetically with `uv run pytest -m conformance`.

## References

- Get repository contents: https://docs.github.com/en/rest/repos/contents
- List commits (with `since`): https://docs.github.com/en/rest/commits/commits
- Get a tree (recursive): https://docs.github.com/en/rest/git/trees
- List repository collaborators: https://docs.github.com/en/rest/collaborators/collaborators
- List repository teams: https://docs.github.com/en/rest/teams/teams
- Repositories (visibility): https://docs.github.com/en/rest/repos/repos
- OAuth app scopes: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps

API version: `X-GitHub-Api-Version: 2026-03-10`. Verified 2026-07-05.
