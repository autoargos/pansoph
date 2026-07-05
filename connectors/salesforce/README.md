# pansoph-connector-salesforce

A pansoph connector for Salesforce records over the REST API.

Implemented against the current official Salesforce REST API documentation (v67.0,
Summer '26; docs verified 2026-07-05). See the header of
`src/pansoph_connector_salesforce/connector.py` for the full checklist of what was
verified and recorded (endpoints, auth and scopes, pagination, incremental sync, rate
limits, ACL model) with doc links.

Supported objects: `Case`, `Account`, `Opportunity`, `Contact`. Only objects whose
read access is governed by the row-level `{Object}Share` table are supported, so the
share-derived ACL is faithful. Salesforce Knowledge (KnowledgeArticleVersion) is
deliberately not supported: its visibility is governed by data categories and
channels, not by share rows, so a share-derived ACL would be wrong.

Incremental: the cursor is the high-water mark of `SystemModstamp`. `list_changes`
selects it, orders ascending, and yields it per record; a later poll filters
`WHERE SystemModstamp > {cursor}`.

ACL note: record access is computed best effort and fail closed from the
`{Object}Share` table (explicit shares only). It reads the correctly named
`{Object}AccessLevel` column per standard object (for example `CaseShare.CaseAccessLevel`).
It does not compute org wide defaults, role hierarchy inheritance, or parent-derived
`ImplicitChild` access (Salesforce no longer stores those rows; use UserRecordAccess
or the parent `AccountShare` to recover it). The result under-reports access, never
over-reports it. See the module header for the full caveat.

It must pass the shared conformance suite (`assert_conforms` from
`pansoph-connector-sdk`) before it merges, and it must map the source permission
model to ACLs faithfully.
