# pansoph Connector Credentials Guide

This guide lists, per platform, how to obtain the credential pansoph needs to sync a connector.

Four platforms (Google, Slack, Atlassian, GitHub) support the one-click Connect flow: you register an OAuth app, set the callback to `http://localhost:8080/api/oauth/{provider}/callback`, and put the client id and client secret into the env vars `PANSOPH_OAUTH_{PROVIDER}_CLIENT_ID` and `PANSOPH_OAUTH_{PROVIDER}_CLIENT_SECRET`. pansoph then handles the token exchange when you click Connect.

All other platforms use the paste path: you paste a token or key into the Add-connector form on the Connectors page. In every case, secrets are sealed into pansoph's secret store and are never stored in plaintext. Always choose read-only scopes.

## Summary Table

| Platform | Auth | Where it goes in pansoph |
|---|---|---|
| Google (Drive, Gmail) | OAuth 2.0 (Web application client) | Interactive OAuth Connect flow. Client ID and client secret go into env vars `PANSOPH_OAUTH_GOOGLE_CLIENT_ID` and `PANSOPH_OAUTH_GOOGLE_CLIENT_SECRET` (or the gitignored `.env.creds`), then the user clicks Connect in the lens UI for the `google_drive` and `gmail` connectors. |
| Atlassian (Jira, Confluence) | OAuth 2.0 (3LO) via pansoph Connect, or Basic auth API token (paste-a-token) | OAuth: env vars `PANSOPH_OAUTH_ATLASSIAN_CLIENT_ID` and `PANSOPH_OAUTH_ATLASSIAN_CLIENT_SECRET`, then click Connect. Token path: paste Email, API token, and Site base URL into the `jira` or `confluence` connector fields on the Add-connector form. |
| Slack | OAuth 2.0 (Connect flow) or bot token (`xoxb-`) paste | OAuth path: `PANSOPH_OAUTH_SLACK_CLIENT_ID` and `PANSOPH_OAUTH_SLACK_CLIENT_SECRET` env vars (or `.env.creds`); redirect `http://localhost:8080/api/oauth/slack/callback`. Paste path: Bot User OAuth Token (`xoxb-`) into the Bot Token field of the `slack` connector in the Add-connector form. |
| GitHub | Personal Access Token (simplest, paste it) or OAuth App (Connect button) | PAT: paste into the `github` connector's token field in the Add-connector form (sealed into the secret store). OAuth App: client id/secret into `PANSOPH_OAUTH_GITHUB_CLIENT_ID` and `PANSOPH_OAUTH_GITHUB_CLIENT_SECRET` (or `.env.creds`). |
| Notion | Internal Integration Token (pasted API key, no OAuth) | Add-connector form (Connectors page) for the `notion` connector: paste the Internal Integration Token into the API token / secret field. Sealed into the secret store. |
| Salesforce | Pasted OAuth credentials (Consumer Key/Secret + instance URL). No pansoph Connect button. | Add-connector form (Connectors page) for the `salesforce` connector: paste Consumer Key, Consumer Secret, and `instance_url`. Sealed into the secret store. |
| Microsoft (SharePoint, Teams) | Microsoft Entra app registration (client credentials): tenant id, client id, client secret, plus target site/drive/team id | Add-connector form fields for the `sharepoint` and `teams` connectors (Connectors page in the lens UI); secrets are sealed into the secret store. |
| Dropbox | OAuth app key/secret plus offline refresh token (pasted into the Add-connector form; pansoph interactive Connect button not supported) | Add-connector form (Connectors page) for the `dropbox` connector: app key, app secret, and refresh token fields; secrets sealed into the secret store. |
| Linear | API key or token pasted into the Add-connector form (no pansoph Connect button) | Add-connector form (Connectors page in the lens UI): paste the value into the connector's API key / token field. Sealed into the secret store. |
| Zendesk | API token (subdomain + agent email + token), pasted into the Add-connector form; OAuth client optional | Add-connector form (`zendesk` connector): subdomain, email, and API token fields, sealed into the secret store. No env vars (Zendesk is not one of the four interactive-OAuth providers). |
| MCP | No pansoph credential (paste an MCP server URL; any server-side auth is configured on the MCP server, not in pansoph) | Add-connector form (Connectors page): `server_url` field, plus optional ACL/scoping fields (`public`, `allow_groups`, `resource_uri_prefix`). No secret, no env var. |

## Google (Drive, Gmail)

Auth type: OAuth 2.0 (Web application client). pansoph's interactive Connect button works for this provider (provider = `google`), so you register one OAuth app in Google Cloud and pansoph handles the token exchange. Both connectors, `google_drive` and `gmail`, share this one Google OAuth app.

Steps (Google Cloud Console):

1. Go to https://console.cloud.google.com and sign in. Create a new project (project picker in the top bar, then New Project) or select an existing one.
2. Enable both APIs. Open APIs & Services > Library (https://console.cloud.google.com/apis/library). Search for "Google Drive API", open it, click Enable. Return to the Library, search for "Gmail API", open it, click Enable.
3. Configure the consent screen. In the left nav open Google Auth Platform > Branding (https://console.cloud.google.com/auth/branding). If prompted with "Get started", set the App name, User support email, and Developer contact email, and choose Audience type External. Save.
4. Add yourself as a test user. Open Google Auth Platform > Audience (https://console.cloud.google.com/auth/audience). While the app is in Testing, under Test users click Add users and add the Google account you will connect with. (Without this, the OAuth consent step is blocked.)
5. Add the scopes. Open Google Auth Platform > Data access (https://console.cloud.google.com/auth/scopes), click Add or remove scopes, and add the two read-only scopes below, then Update and Save.
6. Create the OAuth client. Open Google Auth Platform > Clients (https://console.cloud.google.com/auth/clients), click Create client, and set Application type to Web application. Give it a name.
7. Under Authorized redirect URIs click Add URI and paste the pansoph callback URL (see below). Click Create.
8. Copy the Client ID and Client secret shown in the dialog. The secret is shown once, so save it now. (You can reopen the client later from the Clients page to recopy the ID, but not the secret.)

Read-only scopes to select (least privilege):

- Google Drive: `https://www.googleapis.com/auth/drive.readonly` (view and download Drive files)
- Gmail: `https://www.googleapis.com/auth/gmail.readonly` (view email messages and settings)

Note: both are Google-classified "restricted" scopes, so a Google app-verification / security review is required before non-test users can connect. For local single-user testing you stay in Testing mode and use the test user from step 4.

Redirect URI to register (step 7): `http://localhost:8080/api/oauth/google/callback`
(This matches the default `PANSOPH_PUBLIC_BASE_URL=http://localhost:8080`. If you changed the public base URL, use that host instead.)

Into pansoph: put the Client ID in env var `PANSOPH_OAUTH_GOOGLE_CLIENT_ID` and the Client secret in `PANSOPH_OAUTH_GOOGLE_CLIENT_SECRET` in the gitignored `.env.creds` file (never in this committed guide). Restart pansoph, then in the lens UI Connectors page click Connect on the `google_drive` and `gmail` connectors to run the OAuth flow. No token is pasted for this provider.

Do NOT paste real client IDs or secrets into this file. It is committed. Put them only in `.env.creds`.

## Atlassian (Jira, Confluence)

Auth type: OAuth 2.0 (3LO). The pansoph Connect button works for Atlassian (provider = `atlassian`), covering both the `jira` and `confluence` connectors. A simpler paste-a-token path (Basic auth with an API token) is also documented below.

### Option A: OAuth 2.0 (3LO), the Connect button (recommended)

1. Go to https://developer.atlassian.com and sign in. Click your profile icon (top-right), then select Developer console.
2. Click Create, then choose OAuth 2.0 integration. Give the app a name (for example, "pansoph") and accept the terms, then Create.
3. In the left menu, open Permissions. Next to Jira API, click Add, then click Configure. Open the Classic scopes tab and add the read-only scopes below.
4. Still under Permissions, next to Confluence API, click Add, then Configure, open Classic scopes, and add the read-only Confluence scopes below.
5. In the left menu, open Authorization. Next to OAuth 2.0 (3LO), click Add (or Configure). In the Callback URL field, paste exactly:
   `http://localhost:8080/api/oauth/atlassian/callback`
   Then click Save changes.
6. In the left menu, open Settings. Copy the Client ID and the Secret (click to reveal, secrets are shown once).

Read-only scopes to select (Classic scopes, least privilege):

- Jira: `read:jira-work` (read project and issue data, search issues, attachments, worklogs)
- Jira: `read:jira-user` (view usernames, emails, avatars)
- Confluence: `read:confluence-content.all` (read page content, including body)
- Confluence: `read:confluence-space.summary` (read space info)
- Confluence: `search:confluence` (search content and spaces)
- Plus `offline_access` (required so pansoph can obtain a refresh token and keep syncing). pansoph requests this automatically during the Connect flow, so you do not need to add it as a scope in the console.

Into pansoph: Put the Client ID into `PANSOPH_OAUTH_ATLASSIAN_CLIENT_ID` and the Secret into `PANSOPH_OAUTH_ATLASSIAN_CLIENT_SECRET` (in your environment or the gitignored `.env.creds`). Restart pansoph, open the Connectors page, add the Jira and/or Confluence connector, and click Connect. Approve the consent screen. Tokens are sealed into the secret store automatically.

### Option B: Paste an API token (Basic auth)

1. Go to https://id.atlassian.com/manage-profile/security/api-tokens (this is Profile > Security > API tokens). Sign in and verify your identity if prompted (Atlassian emails a one-time passcode).
2. Click Create API token (for scoped tokens, use Create API token with scopes and select the same read-only scopes listed above).
3. Give the token a name (for example, "pansoph"), pick an expiration (1 to 365 days), then Create.
4. Click Copy to clipboard and save it now. The token is shown only once and cannot be recovered later.
5. Note your Atlassian account email and your site base URL (for example, `https://your-team.atlassian.net`).

Into pansoph: On the Connectors page, add the Jira (and/or Confluence) connector and fill the Add-connector form fields: Email (your Atlassian account email), API token (the token from step 4), and Site base URL (`https://your-team.atlassian.net`). These are sealed into the secret store, never stored in plaintext.

## Slack

Auth type: OAuth 2.0. The pansoph Connect button works for Slack (provider = `slack`), so you can use the interactive Connect flow. A pasted bot token (`xoxb-`) is also supported as an alternative.

Both paths start by creating a Slack app and adding read-only scopes.

1. Go to https://api.slack.com/apps and sign in to the workspace you want to index.
2. Click "Create New App", then choose "From scratch".
3. Enter an app name (for example, "pansoph"), pick the target workspace, and click "Create App". You land on the "Basic Information" page.
4. In the left sidebar, open "OAuth & Permissions".
5. Scroll to "Scopes". Under "Bot Token Scopes", click "Add an OAuth Scope" and add each read-only scope listed below.
6. (OAuth Connect path only) Scroll up to "Redirect URLs", click "Add New Redirect URL", paste the callback URL below, click "Add", then "Save URLs".
7. Scroll to the top of "OAuth & Permissions" and click "Install to Workspace", then click "Allow" on the consent screen.
8. Get your values:
   - Bot token (paste path): back on "OAuth & Permissions", copy the "Bot User OAuth Token" (starts with `xoxb-`).
   - Client ID / Client Secret (OAuth Connect path): open "Basic Information" in the sidebar, find the "App Credentials" section, and copy "Client ID" and "Client Secret" (click "Show" to reveal the secret).

Read-only scopes to add (Bot Token Scopes):

- `channels:read` (view basic info about public channels)
- `channels:history` (view messages in public channels the app is in)
- `groups:read` (view basic info about private channels the app is in)
- `groups:history` (view messages in private channels the app is in)
- `users:read` (view people in the workspace, to resolve author names)

All of the above are view-only. Do not add any `:write`, `chat:write`, or admin scopes. Scopes are additive and cannot be narrowed on an existing token, so if you over-scope, reinstall with a reduced set.

Redirect URL to register (OAuth Connect path): `http://localhost:8080/api/oauth/slack/callback` (this matches the default `PANSOPH_PUBLIC_BASE_URL=http://localhost:8080`). Then put the app's Client ID and Client Secret into `PANSOPH_OAUTH_SLACK_CLIENT_ID` and `PANSOPH_OAUTH_SLACK_CLIENT_SECRET` (or the gitignored `.env.creds`) before starting pansoph.

Into pansoph:

- OAuth Connect path: set `PANSOPH_OAUTH_SLACK_CLIENT_ID` = Client ID and `PANSOPH_OAUTH_SLACK_CLIENT_SECRET` = Client Secret, restart pansoph, then click "Connect" on the `slack` connector and complete the workspace install.
- Paste path: on the Connectors page, add the `slack` connector and paste the Bot User OAuth Token (`xoxb-`) into the "Bot Token" field. It is sealed into the secret store, never stored in plaintext.

## GitHub

Auth type: Personal Access Token (simplest) or OAuth App. The pansoph Connect button works for GitHub (provider = `github`), so you can either paste a token or run the interactive OAuth flow. Pick one path below. Always choose read-only.

### Option A: Fine-grained Personal Access Token (recommended, paste it)

1. Go to https://github.com/settings/personal-access-tokens/new (or click your profile picture, top right, then Settings, then Developer settings in the left sidebar, then Personal access tokens, then Fine-grained tokens, then Generate new token).
2. Enter a Token name (for example, "pansoph read-only").
3. Set Expiration. Choose a date, or select Custom. (Shorter is safer; you will re-issue on expiry.)
4. Under Resource owner, select the user or organization that owns the repositories.
5. Under Repository access, select Only select repositories and choose the repos pansoph should index (or All repositories if you intend to index everything under that owner).
6. Under Permissions, open Repository permissions and set:
   - Contents: Read-only
   - Metadata: Read-only (this is mandatory and is already read-only by default)
   Leave every other permission at No access. (Public repositories are readable with no extra permissions.)
7. Click Generate token, then copy the token value now (it is shown only once).

Into pansoph: In the lens UI, open the Connectors page, add a GitHub connector, and paste the token into the connector's token/API key field. It is sealed into the secret store, never stored in plaintext.

### Option B: OAuth App (for the Connect button)

1. Go to https://github.com/settings/applications/new (or click your profile picture, top right, then Settings, then Developer settings, then OAuth Apps, then New OAuth App).
2. Application name: enter any name (for example, "pansoph").
3. Homepage URL: enter `http://localhost:8080` (matches `PANSOPH_PUBLIC_BASE_URL`).
4. Authorization callback URL: enter exactly `http://localhost:8080/api/oauth/github/callback`.
5. Click Register application.
6. On the app page, copy the Client ID. Then click Generate a new client secret and copy the secret value immediately (it is shown only once).
7. Scopes for OAuth Apps are requested at authorization time, not on this form. The Connect flow requests read-only access (repo contents and metadata); approve only read access when prompted.

Into pansoph: Put the Client ID in `PANSOPH_OAUTH_GITHUB_CLIENT_ID` and the client secret in `PANSOPH_OAUTH_GITHUB_CLIENT_SECRET` (env vars, or the gitignored `.env.creds`). Then use the Connect button for GitHub in the lens UI to complete authorization.

## Notion

Auth type: Internal Integration Token (a pasted API secret). The pansoph interactive "Connect" button does NOT support Notion; you paste the token into the Add-connector form instead.

Notion access is not scope-based like OAuth. Instead, an integration only sees the specific pages and databases you explicitly share with it, using read-only capabilities. That shared set is the entire access boundary.

1. Go to https://www.notion.so/my-integrations and sign in with an account in the workspace you want to index. (You can also reach this via Settings, Connections tab, "Develop your own connections". Note that creating integrations may require a workspace owner.)
2. Click "New integration".
3. Enter a recognizable name (this name shows up when you share pages, so make it clear, e.g. "pansoph read-only"), select the target workspace from the "Associated workspace" dropdown, and set the type to "Internal". Click "Save".
4. Open the "Capabilities" tab for the integration and set least-privilege, read-only permissions:
   - Content Capabilities: enable "Read content" only. Leave "Update content" and "Insert content" OFF.
   - Comment Capabilities: leave both "Read comments" and "Insert comments" OFF (enable "Read comments" only if you specifically need comment text indexed).
   - User Capabilities: select "No user information" (or "User information without email addresses" if display names are needed). Do NOT grant email access.
   - Click "Save changes".
5. Open the "Configuration" tab (or "Secrets"), find the "Internal Integration Token" (also labeled Internal Integration Secret; it starts with "ntn_" or "secret_"), click "Show", then "Copy".
6. Share each page or database you want indexed with the integration. This is required, because an unshared page returns nothing. Open the page or database in Notion, click the "..." menu (top right), choose "Connections" (or "Add connections"), search for your integration by name, and select it. Access cascades to all child pages, so sharing a top-level page grants the whole subtree. Repeat for every top-level page or database you want in scope.

Read-only permissions to select: "Read content" only (Content Capabilities). Everything else OFF, and "No user information" for User Capabilities.

Into pansoph: on the Connectors page, add a Notion connector and paste the Internal Integration Token (from step 5) into the connector's API token / secret field. It is sealed into the secret store, never stored in plaintext. The set of pages and databases you shared in step 6 defines exactly what pansoph can read.

## Salesforce

Auth type: Pasted OAuth app credentials (Consumer Key, Consumer Secret, and your instance URL). The pansoph interactive "Connect" button does NOT support Salesforce, so you register the app yourself and paste the values into the Add-connector form. Read-only is enforced on the integration user's profile/permission set, not by OAuth scope (Salesforce has no granular read-only OAuth scope).

As of Spring '26, Salesforce restricts creating new Connected Apps and steers you to External Client Apps. Use the External Client App path below. (If your org still has Connected App creation enabled, the equivalent steps live under Setup, App Manager, New Connected App.)

Steps (External Client App):

1. Log in to your Salesforce org as an admin. Click the gear icon (top right), then Setup. Setup opens at a URL like `https://<yourdomain>.my.salesforce.com/lightning/setup/SetupOneHome/home`.
2. In the Quick Find box (left), type `External Client App Manager`, then click External Client App Manager.
3. Click New External Client App.
4. Under Basic Information, enter a name (for example, `pansoph`), the API name (auto-fills), and your Contact Email. Leave Distribution State as Local.
5. Expand OAuth Settings and select Enable OAuth.
6. In Callback URL, enter the redirect URL you will use to complete the OAuth flow yourself (for example `http://localhost/callback`). pansoph does NOT perform the Salesforce token exchange, so this is not a pansoph URL; it only has to match the redirect you use when you mint the token out of band.
7. Under Selected OAuth Scopes, add the read-only-appropriate scopes below.
8. Leave Require Proof Key for Code Exchange (PKCE) enabled unless your integration requires it off. Click Create.
9. Open Policies, click Edit, and under App Authorization / refresh settings select Refresh token is valid until revoked. Save.
10. Retrieve credentials: back in External Client App Manager, open your app, go to the Settings tab, expand OAuth Settings, and click Consumer Key and Secret. Verify your identity (a code is emailed to your Contact Email), then copy the Consumer Key and Consumer Secret. (Newly created apps can take up to ~10 minutes to become active.)
11. Note your instance URL (the `https://<yourdomain>.my.salesforce.com` shown in your browser). pansoph needs it as `instance_url`.

Read-only scopes/permissions to select:

- Manage user data via APIs (`api`): required for REST/Bulk API data reads.
- Perform requests at any time (`refresh_token`, `offline_access`): so pansoph can keep syncing without re-login.
- Access unique user identifiers (`openid`): optional, only if identity is needed.

Do NOT add `full` or write-capable scopes. Enforce true read-only by running the integration through a dedicated integration user whose profile or permission set grants Read only (no Create/Edit/Delete) on the objects pansoph should index. Field- and object-level security on that user is what actually limits access; the `api` scope alone does not restrict reads to read-only.

Note: Salesforce is NOT one of pansoph's four Connect-flow providers (google, slack, atlassian, github), so pansoph exposes no `salesforce/callback` endpoint and there are no `PANSOPH_OAUTH_SALESFORCE_*` env vars. You obtain the token yourself (via the Connected App and your own redirect, or the Salesforce CLI) and paste the resulting credentials into the connector form. Set the Connected App's callback URL to whatever redirect you use to mint the token, not a pansoph URL.

Into pansoph: On the Connectors page, add the `salesforce` connector and paste the Consumer Key (Client ID), the Consumer Secret (Client Secret), and your `instance_url` (`https://<yourdomain>.my.salesforce.com`) into the matching Add-connector form fields. These are sealed into pansoph's secret store, never stored in plaintext and never placed in an env var.

## Microsoft (SharePoint, Teams)

Auth type: Microsoft Entra app registration using client credentials (app-only). The pansoph interactive Connect button does NOT work for Microsoft. You paste the values into the Add-connector form for the `sharepoint` and `teams` connectors (secrets are sealed into the secret store, never stored in plaintext).

You need four values plus the target resource id: tenant id, client id, client secret, and the site/drive id (SharePoint) or team/group id (Teams). Always pick read-only permissions.

1. Go to the Microsoft Entra admin center at https://entra.microsoft.com and sign in as a Global Administrator or Privileged Role Administrator (admin consent for application permissions requires this).
2. In the left nav, select Entra ID, expand Applications, then select App registrations.
3. Click New registration. Enter a display name (for example, "pansoph connector").
4. Under Supported account types, select "Accounts in this organizational directory only (single tenant)". Leave Redirect URI empty. Click Register.
5. On the app Overview page, copy the Application (client) ID and the Directory (tenant) ID. Save both.
6. In the left nav select API permissions, then click "+ Add a permission", then Microsoft Graph, then Application permissions.
7. Add the read-only permissions you need, then click "Add permissions":
   - SharePoint (least privilege, recommended): `Sites.Selected` (grants no access until you authorize specific sites in step 9). If you cannot scope per-site, use `Sites.Read.All` instead (tenant-wide read).
   - Teams: `Team.ReadBasic.All`, `Channel.ReadBasic.All`, and `ChannelMessage.Read.All`.
8. Click "Grant admin consent for [your tenant]", then Yes. Confirm each permission shows a green check in the Status column.
9. SharePoint with `Sites.Selected` only: grant the app read access to the specific site. Open Microsoft Graph Explorer (https://developer.microsoft.com/graph/graph-explorer), sign in as a SharePoint admin, set the method to POST, call `https://graph.microsoft.com/v1.0/sites/{siteId}/permissions` with body `{"roles":["read"],"grantedToIdentities":[{"application":{"id":"<client-id>","displayName":"pansoph connector"}}]}`. (Skip this step if you used `Sites.Read.All`.)
10. Create the secret: in the left nav select Certificates & secrets, then Client secrets, then New client secret. Add a description, choose an expiry (12 months or less recommended), and click Add. Copy the secret Value now (it is shown only once and never again).
11. Get the target resource id:
    - SharePoint site id: call `GET https://graph.microsoft.com/v1.0/sites/{hostname}:/{server-relative-path}` (for example `sites/contoso.sharepoint.com:/sites/hr`). The returned `id` has the form `hostname,siteCollectionGuid,webGuid`. For a specific document library, also read the drive id from `GET /sites/{siteId}/drives`.
    - Teams team id: the team id equals the underlying Microsoft 365 group id. Find it via `GET https://graph.microsoft.com/v1.0/groups?$filter=resourceProvisioningOptions/Any(x:x eq 'Team')` or from the Teams admin center.

Least-privilege note: choose `Sites.Selected` over `Sites.Read.All` when you can, and only the three read-only Teams permissions above. Do not add any `*.ReadWrite.*` or `Sites.FullControl.All` scopes.

Into pansoph: On the Connectors page in the lens UI, open the Add-connector form for the `sharepoint` connector (and/or the `teams` connector) and paste: Directory (tenant) ID into the Tenant ID field, Application (client) ID into the Client ID field, the client secret Value into the Client Secret field, and the site/drive id (sharepoint) or team/group id (teams) into the Site/Drive ID or Team ID field. There are no `PANSOPH_OAUTH_MICROSOFT_*` env vars and no redirect/callback URL for Microsoft, because Microsoft is not one of pansoph's four interactive-OAuth platforms; all values go through the Add-connector form.

## Dropbox

Auth type: OAuth app key + app secret + a long-lived offline refresh token, all pasted into the Add-connector form. The pansoph interactive "Connect" button does NOT support Dropbox (it covers only google, slack, atlassian, github), so you generate the refresh token yourself and paste all three values.

1. Go to the App Console at https://www.dropbox.com/developers/apps and click "Create app".
2. For "Choose an API", select "Scoped access".
3. For "Choose the type of access you need", select "Full Dropbox" (or "App folder" if you only need a single app-scoped folder), give the app a unique name, and click "Create app".
4. On the app's settings page, open the "Permissions" tab and enable ONLY the read-only scopes below, then click "Submit" to save:
   - `account_info.read` (enabled by default, required)
   - `files.metadata.read`
   - `files.content.read`
   - `sharing.read`
5. Open the "Settings" tab and copy the "App key" and "App secret" (click "Show" to reveal the secret). The app key is the OAuth client id; the app secret is the client secret.
6. Get a refresh token. Because you re-authorize after adding scopes (adding scopes does NOT apply to existing tokens), do this after step 4. In a browser signed in to the target Dropbox account, visit this authorization URL (replace `<APP_KEY>` with your app key; `token_access_type=offline` is what makes Dropbox return a refresh token):
   ```
   https://www.dropbox.com/oauth2/authorize?client_id=<APP_KEY>&token_access_type=offline&response_type=code&scope=account_info.read%20files.metadata.read%20files.content.read%20sharing.read
   ```
   Click "Allow". Dropbox shows a short one-time authorization code on the page. (No redirect URI is used here, so none needs to be registered in the console.)
7. Immediately (the code expires fast and is single-use) exchange the code for tokens using curl. Replace `<AUTH_CODE>`, `<APP_KEY>`, and `<APP_SECRET>`:
   ```
   curl https://api.dropbox.com/oauth2/token \
     -d code=<AUTH_CODE> \
     -d grant_type=authorization_code \
     -u <APP_KEY>:<APP_SECRET>
   ```
   The JSON response contains `refresh_token`. Copy that value. (The refresh token is long-lived and does not expire unless revoked; the short `access_token` in the response can be ignored, since pansoph refreshes it from the refresh token as needed.)

Read-only scopes to enable (least privilege): `account_info.read`, `files.metadata.read`, `files.content.read`, `sharing.read`. Do not enable any `.write` scopes.

Redirect/callback URL: not applicable. The pansoph Connect flow is not used for Dropbox, so leave the app's OAuth redirect URIs blank and do not set `PANSOPH_OAUTH_DROPBOX_*` env vars (those exist only for the four Connect-supported platforms).

Into pansoph: on the Connectors page, add the "dropbox" connector and paste the App key into the app key field, the App secret into the app secret field, and the `refresh_token` from step 7 into the refresh token field. Save. pansoph seals all three into the secret store (never plaintext) and uses them to mint short-lived access tokens automatically.

## Linear

Auth type: pasted token or API key. The pansoph interactive "Connect" (OAuth) button does NOT support Linear, so you create the credential in Linear and paste it into the Add-connector form. Two options: a Personal API key (simplest, recommended) or an OAuth application (only if you need client id/secret). Always choose the read-only scope.

### Option A: Personal API key (recommended, simplest)

1. Sign in to Linear and open Settings (click your workspace name, top-left, then "Settings"), or go directly to https://linear.app/settings/account/security
2. In the left sidebar under "Account", click "Security & access".
3. Find the "Personal API keys" section and click "New API key".
4. Give the key a descriptive label (for example, "pansoph read-only").
5. For permissions, do NOT grant full access. Select only "Read". Leave "Write", "Admin", "Create issues", and "Create comments" unchecked.
6. (Optional least-privilege step) Restrict the key to only the specific teams pansoph needs, rather than the whole workspace.
7. Click "Create" (or "Create key"), then copy the key immediately. Linear shows it only once. If you lose it, generate a new one.

Note: a personal API key is bounded by your own account's access, so it can only read data your Linear user can already see.

Into pansoph: copy the Personal API key and paste it into the API key / token field of the Linear connector in the Add-connector form (Connectors page in the lens UI). It is sealed into the secret store, never stored in plaintext.

### Option B: OAuth application (only if you need client id/secret)

1. Open https://linear.app/settings/api
2. In the left sidebar under "Account", click "API", then scroll to "OAuth Applications" and click "Create new OAuth Application".
3. Fill in the required fields (application name, developer name, and any icon/description).
4. In "Callback URLs", enter the redirect URL you will use to complete the OAuth flow yourself (for example `http://localhost/callback`), not a pansoph URL. pansoph has no Linear Connect flow, so it does not receive this callback.
5. Click "Save". The page refreshes and shows the "Client ID" and "Client Secret" at the top. Copy both now and store them securely.
6. When you request authorization, use only the `read` scope (read-only access to workspace data). Do not add `write` or `admin`.

Into pansoph: Linear is not one of pansoph's built-in Connect providers, so the OAuth env vars are not wired for it. If you go the OAuth route, complete the OAuth flow yourself and paste the resulting read-scoped access token into the token field of the Linear connector in the Add-connector form. (The Personal API key in Option A is the intended path for pansoph and avoids this.)

## Zendesk

Auth type: API token (subdomain + agent email + token). The pansoph Connect button does NOT work for Zendesk (it is not one of the four interactive-OAuth providers), so you paste the credentials into the Add-connector form. An OAuth client is optional (see the end).

Requires a Zendesk admin. API token access is disabled by default and must be turned on first.

1. Sign in to Zendesk Admin Center at `https://{subdomain}.zendesk.com/admin` (your `{subdomain}` is the part before `.zendesk.com`; note it, you will need it).
2. In the left sidebar, click Apps and integrations, then select APIs, then API configuration.
3. If this is the first time, accept the terms and click Get started.
4. Select the Allow API token access checkbox, then click Save.
5. Go back to APIs and select API tokens, then click Add API token (top right).
6. Enter a description (for example, "pansoph read-only"), then click Save.
7. Copy the full token now and store it somewhere safe. After you close the window the full token is never shown again (only a truncated version).
8. Note the agent email address of the account whose token this is (any active agent account works; use a low-privilege read-only agent for least privilege).

Read-only scoping: Zendesk API tokens do not carry OAuth scopes. Access is bounded by the role of the agent whose email you pair with the token, so use an agent with a read-only or light-agent role (or a custom role limited to viewing tickets and Help Center) rather than an admin. The token authenticates as `{email}/token:{api_token}` over Basic auth.

Into pansoph: in the lens UI, open the Connectors page, choose Add connector, and select Zendesk. Paste the `{subdomain}` into the Subdomain field, the agent email into the Email field, and the copied token into the API Token field, then save. pansoph seals these into the secret store (never stored plaintext). No env vars are used for Zendesk.

Optional (OAuth client instead of a token): in Admin Center go to Apps and integrations, then APIs, then OAuth clients, and click Add OAuth client. Set a Name, keep or edit the Identifier, and add your Redirect URL. Request the read-only Help Center scope `hc:read` (or the general `read` scope) at authorization time, since scopes are passed during the OAuth flow, not set in this form. The Secret is shown in full only once after you save, so copy it immediately. Because pansoph has no interactive Connect flow for Zendesk, this path is only useful if you generate the access token yourself and paste it into the Add-connector form.

## MCP servers (mcp, granola)

Auth type: no pansoph platform credential. pansoph's interactive OAuth "Connect" button does NOT apply here (it only covers google, slack, atlassian, github). Both the `mcp` connector and the `granola` connector are the pansoph MCP bridge: you paste an MCP server URL through the Add-connector form, and any authentication the target MCP server requires is configured on that server, not in pansoph. There is no client id, client secret, token, or env var for pansoph to hold.

The only value pansoph needs is the streamable-HTTP URL of a running MCP server.

1. Decide which MCP server this connector points at.
   - Local demo (`mcp` connector): the bundled sample server runs inside the Docker Compose network as service `mcp-sample` on port 8000. Its in-network URL is `http://mcp-sample:8000/mcp`. Nothing to register; it is unauthenticated and read-only.
   - Granola (`granola` connector): Granola's hosted MCP endpoint is `https://mcp.granola.ai/mcp`. Note the limitation below before choosing this.
   - Any other MCP server: use its own streamable-HTTP endpoint, which by convention ends in `/mcp` (for example `https://your-server.example.com/mcp`).
2. If the target server requires authentication, configure it on the server side (this is not a pansoph step):
   - Many read-only MCP servers (including the local `mcp-sample`) require no auth at all.
   - Servers that do require auth use the MCP authorization spec (OAuth 2.1 with PKCE). Granola's public server uses browser-based OAuth with Dynamic Client Registration, so there is no client id or secret to generate or paste; a user signs in through the browser when the connection is first authorized. When you have a choice, connect the MCP server against a read-only account or role so the bridge can only read.
3. In pansoph, open the Connectors page (lens UI) and click Add connector.
4. Choose `mcp` (generic bridge) or `granola`.
5. Fill the Add-connector form fields:
   - "MCP endpoint" / `server_url` (required): the URL from step 1, for example `http://mcp-sample:8000/mcp` for the local demo, or `https://mcp.granola.ai/mcp` for Granola.
   - "Public to the whole tenant" / `public` (optional): leave off unless every tenant member should read these resources.
   - "Allowed groups" / `allow_groups` (optional): the pansoph groups permitted to read the ingested resources.
   - "Resource URI prefix" / `resource_uri_prefix` (optional): confine this instance to resources whose URI starts with this prefix, so one MCP server can back several scoped connector instances. Leave empty to ingest everything the server exposes.
6. Save. The connector ingests the resources that MCP server exposes as documents.

Read-only scopes/permissions to select: none inside pansoph (the connector has no scope picker). Enforce least privilege on the MCP server itself: point `server_url` at a server whose own auth grants read-only access. For Granola specifically, MCP can reach only your "My notes" (private) space and shared/private-folder notes per your plan; team-space folders are never exposed via MCP, and Granola MCP access is read-only.

Into pansoph: paste the MCP server URL into the "MCP endpoint" (`server_url`) field of the Add-connector form (`http://mcp-sample:8000/mcp` for the local demo, `https://mcp.granola.ai/mcp` for Granola). Set the optional `public`, `allow_groups`, and `resource_uri_prefix` fields as needed. There is no secret to store and no env var to set for this connector.
