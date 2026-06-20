# pansoph-connector-salesforce

A pansoph connector for salesforce (Knowledge articles and Cases).

Implement it against the latest official Salesforce REST API documentation. See the
header of `src/pansoph_connector_salesforce/connector.py` for the checklist of what to
verify and record (endpoints, auth and scopes, pagination, incremental sync, rate
limits, ACL model).

ACL note: record access is computed best effort from the `{Object}Share` table
(explicit shares only). It does not compute org wide defaults or role hierarchy
inheritance, and Knowledge article visibility (data categories) is a known limitation.
See the module header for the full caveat.

It must pass the shared conformance suite (`assert_conforms` from
`pansoph-connector-sdk`) before it merges, and it must map the source permission
model to ACLs faithfully.
