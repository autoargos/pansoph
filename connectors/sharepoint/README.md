# pansoph-connector-sharepoint

A pansoph connector for SharePoint and OneDrive, built on the Microsoft Graph v1.0 API.

## Configuration

| Key | Required | Description |
|---|---|---|
| `token` | yes | An OAuth2 access token (app-only or delegated) with read access to the drive. Acquiring and refreshing it is the operator's step. |
| `drive_id` | yes | The id of the SharePoint document library drive or the user's OneDrive. |
| `base_url` | no | Graph base, default `https://graph.microsoft.com`. Versioned paths (`/v1.0/...`) are added by the connector. |

## What it maps

- Listing: items in the drive root via `/drives/{drive_id}/root/children`, paginated by `@odata.nextLink`.
- Fetch: item metadata (name, webUrl, mime). Binary content extraction is a downstream parsing step.
- ACLs: each item's `permissions` collection. Anonymous and organization scoped sharing links map to `public`. Direct user grants map to `allow_users`, group grants map to `allow_groups`, namespaced by the instance id.

## Caveats

- An "organization" scope link is tenant-wide and is treated as public within the instance namespace. Revisit if an instance spans guests or multiple tenants.
- Group expansion (resolving a group to its member users) is deferred to the authz layer. The connector records the direct group identifier faithfully.

Verified against the official Microsoft Graph docs on 2026-06-20. See the module docstring for the exact endpoints.
