# pansoph-connector-salesforce

A pansoph connector that ingests records of a single Salesforce sObject over the Salesforce REST API (SOQL). It maps each record's row-level Salesforce sharing to pansoph ACLs, fail closed, so a document is never surfaced to a principal the sharing table did not explicitly grant read to.

## What it syncs

One sObject per configured instance, chosen from a fixed allowlist. Only objects whose read access is governed by the row-level `{Object}Share` table are supported, so the share-derived ACL is faithful. The supported objects and the standard fields ingested as title and body:

| Object | Title field | Body field | Share table |
|---|---|---|---|
| `Case` (default) | `Subject` | `Description` | `CaseShare` |
| `Account` | `Name` | `Description` | `AccountShare` |
| `Opportunity` | `Name` | `Description` | `OpportunityShare` |
| `Contact` | `Name` | `Description` | `ContactShare` |

Each document text is `"{title}\n{body}"`, mime `text/plain`. Salesforce Knowledge (`KnowledgeArticleVersion`) is deliberately not offered: its visibility is governed by data categories and channels, not by share rows, so a share-derived ACL would be wrong. `validate()` rejects any object outside the allowlist.

## Setup

Salesforce access uses a Connected App configured for OAuth 2.0.

1. In Salesforce Setup, create a Connected App (App Manager, New Connected App) and enable OAuth settings.
2. Set a callback (redirect) URL for the OAuth flow you run, matching the pattern `https://<your-host>/oauth/callback`. This connector does not run the flow itself, so use whatever callback your token-acquisition tooling expects.
3. Select the OAuth scopes listed below.
4. Acquire an OAuth 2.0 access token using any flow you prefer (JWT bearer, web server, or other). This connector consumes a token you supply; it does not mint or refresh tokens.

Configuration keys (from `spec()`):

| Key | Kind | Required | Notes |
|---|---|---|---|
| `instance_url` | string | yes | Your org's My Domain base URL, e.g. `https://acme.my.salesforce.com`. All REST calls are made against it. |
| `token` | secret | yes | OAuth 2.0 bearer access token. Sent as `Authorization: Bearer <token>`. Sealed in the secret store. |
| `object` | string | no | The sObject to ingest, one of the supported objects above. Defaults to `Case`. |

REST calls target API path `/services/data/v67.0` on `instance_url`.

## Required scopes / permissions

The connector consumes a bearer token; it does not enumerate scopes in code. The token must be able to run SOQL queries and retrieve records for the configured object and to read that object's `{Object}Share` table. Grant the Connected App the OAuth scopes needed for REST API access to those sObjects (typically `api`, plus `refresh_token`/`offline_access` if your token-acquisition flow refreshes). Beyond OAuth scope, the credential's Salesforce profile and permission sets decide which records and share rows it can actually read; the mapped ACL reflects exactly that reach.

## Permission model (ACL mapping)

Security critical. Read this before trusting the ACL.

For each record, the connector queries the object's share table:

```sql
SELECT UserOrGroupId, {Object}AccessLevel, RowCause
FROM {Object}Share
WHERE {Object}Id = '<record_id>'
```

It reads the correctly named `{Object}AccessLevel` column per standard object (`CaseShare.CaseAccessLevel`, `AccountShare.AccountAccessLevel`, and so on), never a bare `AccessLevel` (which exists only on custom objects and would 400 on a standard object).

`{Object}AccessLevel` is a restricted picklist with documented values `None`, `Read`, `Edit`, `All`. A row is mapped to an allow principal only when its level is a read grant (`Read`, `Edit`, `All`). `None`, empty, or any unrecognized value is skipped. `None` is a documented valid value that grants NO read; Salesforce uses it to carry a sibling column's implicit access on the same row (for example an `AccountShare` row with `AccountAccessLevel="None"` but `CaseAccessLevel="Read"`). Mapping such a row would over-share, so the connector fails closed and skips it. (Regression pinned by `test_salesforce_regress_2026_07_05_none_access_level_not_mapped`.)

For a mapped principal, a `005` key prefix on `UserOrGroupId` is a User; anything else (Group, Role, RoleAndSubordinates, PublicGroup) is a group. Sharing in Salesforce is additive, so there is no deny; `deny` stays empty and `public` is false. The ACL namespace is the instance id (`principal_ns = instance_id`), so instances never collide.

This is a deliberately conservative subset of true effective access. The share table lists only explicit shares. It does not encode the org-wide default, role-hierarchy inheritance, or owner/manager implicit access. Since the "Enable Faster Account Sharing Recalculation" release update, Salesforce no longer stores `ImplicitChild` share rows for Account, Case, Contact, and Opportunity, so parent-derived access (seeing a child Case because you can see its Account) is invisible to a share-table read. The computed ACL can under-report access, never over-report it. Recovering the missing access would require `UserRecordAccess` or the parent `AccountShare` column, which this connector does not read.

## Sync

Listing (`list_changes`) issues SOQL:

```sql
SELECT Id, SystemModstamp, {title_field}, {body_field} FROM {Object}
[ WHERE SystemModstamp > {cursor} ]
ORDER BY SystemModstamp ASC
```

Incremental cursor: `SystemModstamp`, the system audit field that updates on every change. The connector selects it, orders ascending, and yields each record's normalized `SystemModstamp` (`YYYY-MM-DDTHH:MM:SSZ`) as the cursor. A later poll filters `WHERE SystemModstamp > {cursor}`. `SystemModstamp` is second precision, so a record on the boundary second can be re-yielded; re-ingest is idempotent (yields at-least-once, never drops).

Pagination follows the SOQL `nextRecordsUrl` (queryMore) until `done` is true. `fetch` retrieves a record via `GET /sobjects/{Object}/{id}` and returns its text plus the ACL. Record ids are validated against the Salesforce id shape (15 or 18 alphanumerics) before any interpolation into SOQL, so a non-id `source_doc_id` is rejected rather than executed.

## Known limits

- **Restriction rules are not exposed by the API.** Salesforce restriction rules can narrow which records a user sees, but they are not reflected in the `{Object}Share` rows this connector reads and there is no API surface to detect them. The connector cannot see them and stays conservative (it can only under-report access, so this does not cause a leak).
- **Deletions are not modeled** (`deletions=False`). Salesforce exposes sObject Get Deleted, but it reads a delete log Salesforce purges after a short window, so a poll-based tombstone stream is not reliably complete. It is declared false rather than half-implemented.
- **`ImplicitChild` parent-derived access is invisible** to a share-table read (see Permission model); under-reported, not leaked.
- **Token lifecycle is the operator's responsibility.** This connector consumes a bearer token and does not mint or refresh it.
- **Webhooks / change delivery are not modeled** (`on_webhook` returns nothing). Platform Events, Change Data Capture, and outbound messages are not wired up; freshness comes from the `SystemModstamp` incremental query.
- The org's daily API request allocation applies and is surfaced, not hidden.

## Conformance

This connector passes the shared conformance suite (`assert_conforms` from `pansoph-connector-sdk`), run hermetically with `uv run pytest -m conformance`.

## References

Verified against the current official Salesforce REST API docs on 2026-07-05, targeting REST API v67.0 (Summer '26). Base: `https://developer.salesforce.com`.

- REST API intro: `/docs/atlas.en-us.api_rest.meta/api_rest/intro_what_is_rest_api.htm`
- API versions (v67.0 current): `/docs/atlas.en-us.api_rest.meta/api_rest/dome_versions.htm`
- SOQL query: `/docs/atlas.en-us.api_rest.meta/api_rest/dome_query.htm`
- queryMore: `/docs/atlas.en-us.api_rest.meta/api_rest/dome_querymore.htm`
- sObject retrieve: `/docs/atlas.en-us.api_rest.meta/api_rest/dome_sobject_retrieve.htm`
- sObject Get Deleted: `/docs/atlas.en-us.api_rest.meta/api_rest/dome_get_deleted.htm`
- CaseShare object: `/docs/atlas.en-us.object_reference.meta/object_reference/sforce_api_objects_caseshare.htm`
- AccountShare object: `/docs/atlas.en-us.object_reference.meta/object_reference/sforce_api_objects_accountshare.htm`
- Org-wide defaults / data sharing: `/docs/atlas.en-us.securityImplGuide.meta/securityImplGuide/security_data_sharing.htm`
