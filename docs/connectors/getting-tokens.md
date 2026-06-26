# Getting connector tokens

How to obtain a least-privilege credential for each pansoph connector. Verified against
each provider's current (2026) official documentation. No em dashes anywhere, per house
style.

## How pansoph handles credentials (read this first)

You provision a credential with a provider ONCE. You hand pansoph the durable credential
(a refresh token, a service-account key, a client secret, or a long-lived API token).
pansoph stores it encrypted in its secret store (envelope encryption, never in plaintext
config or logs) and the auth subsystem mints and refreshes short-lived access tokens
automatically for every sync. You never paste a raw, hourly-expiring access token.

Two consequences:

- For OAuth providers (Google, Microsoft, Salesforce, Slack, Atlassian, Notion, Dropbox,
  Linear, Zendesk) you complete a one-time authorization to get a refresh token (or a
  service-account key / client secret). pansoph refreshes from there.
- Always grant the least privilege listed. A read-only knowledge connector never needs any
  write, manage, or full-control permission. Do not grant them.

Security rules: store nothing secret in the repo. Provide secrets to pansoph through the
secret store (or the gitignored `.env.creds` during setup). Use a dedicated, least-
privileged service account per source, not your personal admin account.

## Quick reference

| Connector | Auth model | What you create | Long-lived without refresh? |
|---|---|---|---|
| github | GitHub App (recommended) or fine-grained PAT | App + private key, or a PAT | PAT: yes. App: mints from key |
| google_drive | Service account + domain-wide delegation (Workspace), or OAuth refresh token | SA JSON key, or OAuth client + refresh token | SA key: yes |
| gmail | Same as Drive (OAuth refresh token, or SA + DWD) | OAuth client + refresh token, or SA key | SA key: yes |
| slack | OAuth v2 with token rotation | Slack app + refresh token | rotating |
| confluence | OAuth 2.0 3LO (refresh token) or Basic API token | Atlassian app + refresh token, or API token | API token: yes |
| jira | OAuth 2.0 3LO (refresh token) or Basic API token | Atlassian app + refresh token, or API token | API token: yes |
| notion | Internal integration token (single workspace) or OAuth | Integration + token | internal token: yes |
| salesforce | Client credentials or JWT bearer | External Client App + secret or certificate | mints from secret/cert |
| sharepoint | Microsoft Graph app-only (client credentials) | Entra app registration + secret/cert | mints from secret/cert |
| teams | Microsoft Graph app-only (client credentials) | Entra app registration + secret/cert | mints from secret/cert |
| dropbox | OAuth refresh token | Dropbox app + refresh token | refresh |
| linear | OAuth refresh token (or personal API key) | Linear OAuth app + refresh token | API key: yes |
| zendesk | OAuth refresh token (or API token) | Zendesk OAuth client + refresh token | API token: yes |
| weblink | none | nothing (public pages only) | n/a |

---

# The four you are providing first

## github

**Auth:** GitHub App installation token (recommended, org-wide, least privilege) or a
fine-grained PAT (single-owner, content-only).

**Least-privilege permissions (GitHub App, Read-only each):** Repository `Contents`,
Repository `Metadata`; for ACL sync add Repository `Administration` (lists collaborators
and teams) and Organization `Members` (expands teams to users).

> Important: listing collaborators/teams requires more than content read. A fine-grained
> PAT with only Contents+Metadata can read files but CANNOT list collaborators or teams,
> so private-repo ACL sync is limited. Use a GitHub App (or a write-level token) for full
> ACL fidelity.

**Steps (GitHub App):**
1. github.com > Settings > Developer settings > GitHub Apps > New GitHub App.
2. Repository permissions: Contents = Read-only, Metadata = Read-only. For ACL sync add
   Administration = Read-only and Organization permission Members = Read-only. Nothing else.
3. Subscribe to the Push event and set a Webhook secret (store it in the secret store).
4. Create the app; note the App ID. Generate a private key (.pem) and store it in the
   secret store.
5. Install the app on only the repos to index; capture the installation_id.

**Steps (fine-grained PAT, simpler, content-only):** Settings > Developer settings >
Personal access tokens > Fine-grained > Generate. Resource owner = your org; Repository
access = only the target repos; Repository permissions Contents = Read-only, Metadata =
Read-only. Set a short expiry. Copy once.

**Config you give pansoph:** `owner`, `repo`; then either App auth (`app_id`,
`installation_id`, `private_key`) or `token` (the PAT). `base_url` only for GitHub
Enterprise Server.

**Docs:** https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens
and https://docs.github.com/en/apps/creating-github-apps

## google_drive and gmail

**Auth:** Two options. For a Google Workspace org, a **service account with domain-wide
delegation** (DWD) is the right answer (no per-user consent, the SA key is the only durable
secret, it does not expire). For a single account or non-Workspace, an **OAuth refresh
token**.

**Least-privilege scopes (RESTRICTED):**
- Drive content: `https://www.googleapis.com/auth/drive.readonly`
- Gmail content: `https://www.googleapis.com/auth/gmail.readonly`

> These are Google RESTRICTED scopes. An externally distributed app needs Google OAuth app
> verification plus an annual CASA security assessment. Internal org-only use can rely on
> the internal-app path. Also: an OAuth refresh token is revoked after 7 days while the app
> is in "Testing"; move the app to "In production" so the refresh token persists.

**Steps (service account + DWD, recommended for Workspace):**
1. Google Cloud Console: create/select a project; enable the Drive API and/or Gmail API.
2. Create a service account; create a JSON key (store it in the secret store). Note its
   numeric Client ID.
3. As a Workspace super admin: Admin console > Security > Access and data control > API
   controls > Domain-wide delegation > Add new. Enter the SA Client ID and authorize
   exactly `drive.readonly` and/or `gmail.readonly`. Save. (A second super admin may need
   to approve; propagation can take up to 24h.)

**Steps (OAuth refresh token, single user):**
1. Cloud Console: enable the API; create an OAuth client ID (Desktop or Web).
2. On the consent screen add only the `*.readonly` scope; move the app to Production.
3. Run the authorization-code flow with `access_type=offline&prompt=consent`; exchange the
   code at `https://oauth2.googleapis.com/token`; capture the `refresh_token`.

**Config you give pansoph:** DWD: `service_account_json`, `subject` (the user email to
impersonate). OAuth: `client_id`, `client_secret`, `refresh_token`. Gmail also: `user_email`.
Drive scoping: optional `folder_id` and/or `drive_id`.

**Docs:** https://developers.google.com/workspace/drive/api/guides/about-auth and
https://developers.google.com/identity/protocols/oauth2/service-account

## slack

**Auth:** OAuth v2 with token rotation (production default). A static bot token works for
legacy non-rotating installs.

**Least-privilege bot scopes (read-only):** `channels:read`, `groups:read`, `mpim:read`,
`im:read`, `channels:history`, `groups:history`, `mpim:history`, `im:history`,
`users:read` (resolve user ids to identities for ACLs).

**Steps:**
1. https://api.slack.com/apps > Create New App > From scratch; pick the workspace.
2. OAuth & Permissions > Bot Token Scopes: add the nine scopes above.
3. OAuth & Permissions > toggle ON Token Rotation (irreversible). Note `client_id` and
   `client_secret` from Basic Information.
4. Set a Redirect URL, install the app (or run the OAuth v2 authorize flow), and capture
   the initial `refresh_token` (xoxe prefix) from `oauth.v2.access`.
5. The bot must be invited (`/invite @yourapp`) into each private channel, group DM, and DM
   you want indexed. Public channels need no invite.

**Config you give pansoph:** `client_id`, `client_secret`, `refresh_token`, `instance_id`.

**Docs:** https://docs.slack.dev/authentication/tokens and
https://api.slack.com/authentication/rotation

## confluence and jira (Atlassian)

**Auth:** OAuth 2.0 3LO with a rotating refresh token (recommended) or Basic auth with a
scoped API token (single-tenant self-host fallback). One Atlassian account/app can serve
both products.

**Least-privilege scopes:**
- Confluence: `read:page:confluence`, `read:space:confluence`,
  `read:content-details:confluence`, `search:confluence`, `read:confluence-user`, plus
  `offline_access` (to get a refresh token).
- Jira: `read:jira-work`, `read:jira-user`, plus `offline_access`. (Optional
  `manage:jira-webhook` only if you register deletion/update webhooks.)

> OAuth access is still capped by the authorizing account's own Atlassian permissions.
> Use a dedicated service account with view access to exactly the spaces/projects to index.

**Steps (OAuth 3LO):**
1. https://developer.atlassian.com/console/myapps/ > create an app.
2. Add the Confluence and/or Jira product; under Permissions add only the read scopes above
   plus `offline_access`. No write scopes.
3. Authorization > set the OAuth 2.0 (3LO) callback URL.
4. Have the service account complete the consent flow at `https://auth.atlassian.com/authorize`;
   exchange the code at `https://auth.atlassian.com/oauth/token`; save the `refresh_token`.
5. Resolve the `cloud_id`: GET `https://api.atlassian.com/oauth/token/accessible-resources`.

**Steps (Basic API token fallback):** the service account creates a SCOPED API token at
https://id.atlassian.com/manage-profile/security/api-tokens, granting only the read scopes
above. Configure `site_base_url` (e.g. `https://acme.atlassian.net`), `email`, `api_token`.

**Config you give pansoph:** OAuth: `client_id`, `client_secret`, `refresh_token`,
`cloud_id`; plus `space_keys` (Confluence) / `project_key(s)` (Jira). Basic: `site_base_url`,
`email`, `api_token`.

**Docs:** https://developer.atlassian.com/cloud/confluence/rest/v2/intro/ and
https://developer.atlassian.com/cloud/jira/platform/scopes-for-oauth-2-3LO-and-forge-apps/

---

# The rest (for whenever you want them)

## notion

**Auth:** Internal integration token (single workspace, simplest) or OAuth (multi-workspace).
**Least privilege:** capability "Read content" only (no Update/Insert). User information set
to "No user information" unless you need principal mapping.
**Steps (internal):** notion.so integrations > New internal integration in the target
workspace > Capabilities = Read content only > copy the Internal Integration Secret. Then
share each page/database to ingest with the integration (page ... menu > Connections). The
shared set IS the access boundary.
**Config:** `token` (auth_mode=static), plus the instance ACL (`public`/`allow_groups`).
**Docs:** https://developers.notion.com/reference/intro

## salesforce

**Auth:** OAuth client credentials (simplest unattended) or JWT bearer (certificate-signed).
**Least privilege:** scope `api` only (NOT `full`); the real read limit is the integration
user's permission set. Create a dedicated API-Only integration user with Read on only the
target objects and their share tables.
**Steps:** create the integration user + read-only permission set; Setup > App Manager >
create an External Client App; enable Client Credentials Flow with that run-as user and the
`api` scope (or JWT with an uploaded X.509 cert). Note the Consumer Key/Secret.
**Config:** `instance_url`, `client_id`, `client_secret` (or `jwt_private_key` +
`integration_user`), `object`, `api_version`.
**Docs:** https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/

## sharepoint and teams (Microsoft Graph)

**Auth:** Microsoft Graph app-only (client credentials grant). One Entra app can serve both.
**Least-privilege application permissions (read-only):**
- SharePoint/OneDrive: `Sites.Selected` (then grant the app read on each specific site) is
  tightest; `Files.Read.All` or `Sites.Read.All` are broader.
- Teams: `Channel.ReadBasic.All`, `ChannelMessage.Read.All`, `ChannelMember.Read.All`,
  `TeamMember.Read.All` (needed for standard-channel ACLs).
**Steps:** Microsoft Entra admin center > App registrations > New registration (single
tenant). Add a certificate (preferred) or client secret. API permissions > Microsoft Graph
> Application permissions > add the least-privilege set above > Grant admin consent. For
`Sites.Selected`, grant the app read per site via `POST /sites/{siteId}/permissions`.
**Config:** `tenant_id`, `client_id`, `client_secret` or `client_certificate`; SharePoint:
`site_id`/`drive_id`; Teams: `team_id`.
**Docs:** https://learn.microsoft.com/graph/auth-v2-service

## dropbox

**Auth:** OAuth refresh token (`token_access_type=offline`).
**Least-privilege scopes:** `account_info.read`, `files.metadata.read`, `files.content.read`,
`sharing.read` (team apps add `team_info.read`, `members.read`, `groups.read`).
**Steps:** https://www.dropbox.com/developers/apps > Create app > Scoped access > pick
access type. Permissions tab > enable the read scopes above. Build the authorize URL with
`token_access_type=offline`, approve, exchange the code at
`https://api.dropboxapi.com/oauth2/token` for a `refresh_token`.
**Config:** `app_key`, `app_secret`, `refresh_token`, `path`.
**Docs:** https://developers.dropbox.com/oauth-guide

## linear

**Auth:** OAuth refresh token (scope `read`) or a personal API key (single workspace).
**Least privilege:** scope `read` only (never write/admin). client_credentials only sees
public teams, so use authorization-code with an account that is a member of every team to
index (including private).
**Steps:** linear.app/settings/api > OAuth applications > Create new; set a redirect URI.
Authorize at `https://linear.app/oauth/authorize?...&scope=read`; exchange at
`https://api.linear.app/oauth/token` for a `refresh_token`.
**Config:** `client_id`, `client_secret`, `refresh_token` (auth_mode=oauth2).
**Docs:** https://linear.app/developers/oauth-2-0-authentication

## zendesk

**Auth:** OAuth refresh token (or an API token fallback).
**Least-privilege scope:** `hc:read` (Help Center read). The authorizing identity must be
an agent or Help Center manager. Use a dedicated least-privileged service agent.
**Steps:** Admin Center > Apps and integrations > APIs > Zendesk API > OAuth Clients > Add.
Run the authorization_code flow at `https://{subdomain}.zendesk.com/oauth/authorizations/new?...&scope=hc:read`;
exchange at `https://{subdomain}.zendesk.com/oauth/tokens` for a `refresh_token`.
**Config:** `subdomain`, `client_id`, `client_secret`, `refresh_token`, `scope=hc:read`.
**Docs:** https://developer.zendesk.com/api-reference/

## weblink

**Auth:** none. The weblink connector fetches only openly reachable public pages over HTTPS
and never bypasses authentication. There is no token to acquire.
**What to set instead:** a descriptive `user_agent` (with a contact URL), an `allowed_hosts`
egress allowlist, `respect_robots=True` (mandatory), and a polite `min_delay_seconds`. Only
configure URLs that load with no login in a fresh private browser session, and whose terms
of service permit automated access. Default `public=False`; open access only via
`allow_groups`/`allow_users`.
**Docs:** RFC 9309 (robots), RFC 9110 (HTTP), https://www.sitemaps.org/protocol.html

---

## Where to put the values

During setup, place secret values in the gitignored `.env.creds` (it matches `.env.*` in
`.gitignore`). pansoph reads them into its encrypted secret store; they are never committed.
Tell the maintainer (or the agent) which connectors you have provisioned and the targeting
(which repos, spaces, projects, sites, channels), and each instance is created and synced,
then verified with the permission red-team to prove its source ACLs map faithfully.
