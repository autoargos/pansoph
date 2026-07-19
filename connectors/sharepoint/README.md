# pansoph-connector-sharepoint

A pansoph connector for SharePoint document libraries and OneDrive drives, built on the Microsoft Graph v1.0 API. It ingests file metadata and each item's permissions, mapping the source ACLs into pansoph's access control model so retrieval only ever returns what a principal is allowed to see.

## What it syncs

- Drives. A single SharePoint document library drive or a OneDrive drive, selected by its Graph `drive_id`.
- Documents. Every item in the drive is enumerated through the delta feed, which walks the full drive hierarchy (nested folders included), not just the root's immediate children.
- Metadata. On `fetch`, per-item `name` (title), `webUrl` (source URL), and the file `mimeType`. Binary content extraction is left to a downstream parsing step; the connector carries metadata and the ACL only.

## Setup

Auth is OAuth2. The connector does not mint or refresh tokens: the operator supplies a Microsoft Graph access token and the connector sends it as `Authorization: Bearer <token>`. It never sees the client secret.

1. Register an application in Microsoft Entra ID (Azure AD) for your tenant.
2. Grant it Graph permission `Files.Read.All` or `Sites.Read.All` (see below).
3. Acquire an access token via the OAuth2 client credentials flow against the tenant (app-only), or a delegated token. Refreshing it on schedule is the operator's step.
4. Find the target drive's Graph `drive_id` (the SharePoint library drive or the user's OneDrive).
5. Configure a connector instance with the fields below.

Config fields (from `spec()`):

| Key | Required | Kind | Description |
|---|---|---|---|
| `token` | yes | secret | Graph OAuth2 access token (Bearer). Minted and refreshed by the operator. Sealed in the secret store. |
| `drive_id` | yes | string | The Graph drive id of the SharePoint library or OneDrive to ingest. |
| `base_url` | no | string | Graph base, default `https://graph.microsoft.com`. Override only for a sovereign cloud or a proxy. Stop before `/v1.0`; the connector adds versioned paths. |

`validate` fails closed if `token` or `drive_id` is missing. Each configured instance carries its own token and its own ACL namespace (`principal_ns = instance_id`), so instances never collide.

## Required scopes / permissions

The connector maps whatever the supplied credential can reach; it does not assume a scope. The application permission the operator grants in Entra is one of:

- `Files.Read.All`
- `Sites.Read.All`

## Permission model (ACL mapping)

Each `driveItem` carries a `permissions` collection, read from `GET /drives/{drive_id}/items/{item_id}/permissions`. The connector maps it into a pansoph `AccessControl` (namespaced by `instance_id`), which is UNION-only (`public` OR `allow_users` OR `allow_groups`). This is security-critical; the exact behavior:

- Sharing link scope `anonymous` is genuinely world-readable, so it is the only scope that sets `public=True`.
- Sharing link scope `organization` means link-bearing tenant members excluding B2B guests, which is an AND (tenant member AND holds the link) that a UNION-only model cannot express. It fails closed: it maps to a stable synthetic membership group `_ORG_MEMBER_GROUP` (`"sharepoint:org-members"`), never to `public=True`. Until the identity layer materializes that group it reaches no principal, so the document under-shares, which is safe. Mapping org scope to public would over-share to every principal including guests, which is the exact bug this closes.
- Sharing link scopes `existingAccess` and `users` grant nothing beyond the item's existing per-identity grants, so they never set public.
- Direct grants live in `grantedToV2` and, for link permissions, the `grantedToIdentitiesV2` array, as SharePoint identity sets. `user` / `siteUser` map to `allow_users`; `group` / `siteGroup` / `sharePointGroup` map to `allow_groups`. Principal id preference: `email`, else `id`, else `loginName`.
- Deny stays empty. The Graph permission resource is grant-only (`grantedToV2`, `grantedToIdentitiesV2`, `link`, `roles`); there is no deny facet.
- Group expansion is deferred. A group grant records the direct group identifier; resolving it to transitive member users is left to the authz layer.

## Sync

Incremental sync uses the Graph delta query: `GET /drives/{drive_id}/root/delta`.

- First sync (cursor is `None`) enumerates the whole drive hierarchy.
- Later syncs pass the saved `@odata.deltaLink` and receive only what changed, so `incremental=True` is honest.
- Pagination follows `@odata.nextLink` across the change set; the terminal page instead carries `@odata.deltaLink`, which the runtime persists as the durable cursor. Non-terminal pages yield an empty cursor so a mid-set page is never persisted as a resume point.
- Deletions: a delta item bearing the `deleted` facet is a tombstone, yielded as a `DocRef` with `deleted=True` (id only, content never fetched), so `deletions=True` is honest. The `deleted` facet wins over any file or folder facet on the same entry.
- Webhooks: Graph change notifications carry only a resource pointer, not item bodies, so `on_webhook` returns nothing and the runtime re-syncs via delta.

## Known limits

- Source-side Deny and broken inheritance are a Graph fidelity limit and are not surfaced here. The `driveItem` permissions collection is grant-only, so SharePoint classic "Deny" permission levels and limited-access / broken-inheritance states are not exposed by it. An item with a surfaced grant plus an unsurfaced source-side Deny is over-granted relative to SharePoint. The connector cannot detect this from the Graph permissions API, so `deny` stays empty; closing it would require a different API surface (SharePoint REST role assignments) and is out of this connector's reach.
- `organization` scope links under-share by design (see the ACL mapping) until the identity layer materializes the org-members group. Under-sharing is a UX cost accepted to avoid over-sharing.
- Group expansion is deferred to the authz layer; the connector records only the direct group identifier.

## Conformance

This connector passes the shared conformance suite (`assert_conforms` from `pansoph-connector-sdk`), run hermetically with `uv run pytest -m conformance`.

## References

Microsoft Graph v1.0 (base `https://graph.microsoft.com/v1.0`). Verified against the official docs on 2026-07-05.

- [driveItem: delta](https://learn.microsoft.com/en-us/graph/api/driveitem-delta?view=graph-rest-1.0) — full-hierarchy enumeration, incremental cursor, deletion signal.
- [deleted facet](https://learn.microsoft.com/en-us/graph/api/resources/deleted?view=graph-rest-1.0) — the tombstone marker on a delta item.
- [driveItem: get](https://learn.microsoft.com/en-us/graph/api/driveitem-get?view=graph-rest-1.0) — get item metadata.
- [driveItem: list permissions](https://learn.microsoft.com/en-us/graph/api/driveitem-list-permissions?view=graph-rest-1.0) — list permissions.
- [permission resource](https://learn.microsoft.com/en-us/graph/api/resources/permission?view=graph-rest-1.0) — grant-only, no deny facet.
- [sharingLink resource](https://learn.microsoft.com/en-us/graph/api/resources/sharinglink?view=graph-rest-1.0) — scope enum: anonymous, organization, existingAccess, users.
- [sharePointIdentitySet](https://learn.microsoft.com/en-us/graph/api/resources/sharepointidentityset?view=graph-rest-1.0) — user, siteUser, group, siteGroup, sharePointGroup.
